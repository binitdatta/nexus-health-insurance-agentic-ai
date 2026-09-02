"""
Prompt + tool-use schema definitions for the Gateway's three LLM
operations. The Gateway itself is department-agnostic: each department
chatbot supplies its own retrieved rows/knowledge-doc snippets in
`retrieved_context`, and these prompts just tell Claude how to reason
over whatever context it's handed.

Structured output is enforced via tool-use (forced tool_choice) rather
than "please respond in JSON" prose instructions — far more reliable to
parse and matches how a production integration should be built.
"""

INTENT_DETECTION_SYSTEM_TEMPLATE = """You are the intent-classification stage of an internal
enterprise assistant for a nationwide health insurance company. You will be given
a department name and a user's message (optionally with recent conversation
history). Classify the user's intent and extract any structured entities
(record identifiers, date ranges, statuses, member/provider IDs, etc.) that a
downstream retrieval step could use to fetch the right data. Use the exact
entity key names specified in the record_intent tool's schema — in particular,
THIS department's own record identifier is '{id_field}' (e.g. '{id_example}'):
never substitute a different department's identifier field name (such as
'claim_number') for it, and never invent another synonym. Do not answer the
user's question here — only classify and extract. Always call the
record_intent tool with your result."""

# Every department chatbot's own retrieve_node looks for a specific
# identifier key on its own domain table (see each chatbot's `_entity()`
# helper) — but until this fix, INTENT_TOOL's entities schema hardcoded a
# single example ('claim_number') for every department, and the LLM
# reasonably defaulted to it even outside Claims, since it was the only
# concrete example ever shown. This was caught via live manual testing
# through the real model — every automated test in this platform mocks
# the Gateway's response directly, so none of them exercised the real
# LLM's entity-key choice. This mapping lets the intent-classification
# prompt/tool tell the model the REAL canonical key for whichever
# department is actually asking.
DEPARTMENT_ID_FIELDS = {
    "CLAIMS": ("claim_number", "CLM100005"),
    "PRIORAUTH": ("pa_number", "PA200000"),
    "NURSING": ("case_number", "NC300000"),
    "CALLCENTER": ("call_reference", "CALL400000"),
    "BILLING": ("invoice_number", "INV500000"),
    "FACPROV": ("provider_code", "PRV1000"),
    "ADJUDICATION": ("claim_number", "CLM100000"),
    "FINANCE": ("txn_reference", "TXN600000"),
    "MANAGEMENT": ("report_ref", "RPT700000"),
    "MEMBERSVC": ("ticket_number", "TIX800000"),
}
_DEFAULT_ID_FIELD = ("record_number", "the record's own identifier")


def _id_field_for(department: str) -> tuple[str, str]:
    return DEPARTMENT_ID_FIELDS.get((department or "").upper(), _DEFAULT_ID_FIELD)


def build_intent_detection_system(department: str) -> str:
    id_field, id_example = _id_field_for(department)
    return INTENT_DETECTION_SYSTEM_TEMPLATE.format(id_field=id_field, id_example=id_example)


def build_intent_tool(department: str) -> dict:
    id_field, id_example = _id_field_for(department)
    return {
        "name": "record_intent",
        "description": "Record the classified intent and extracted entities for the user's message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "data_lookup",
                        "policy_question",
                        "create_record",
                        "update_record",
                        "summarize",
                        "dashboard_metric",
                        "chitchat",
                        "other",
                    ],
                    "description": "The primary thing the user is trying to do.",
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "entities": {
                    "type": "object",
                    "description": "Free-form key/value entities extracted from the message, using these exact "
                    "key names whenever the concept applies — do not invent synonyms for these: "
                    f"'{id_field}' (THIS department's own record identifier, e.g. '{id_example}' — "
                    "never a different department's identifier field name such as 'claim_number' "
                    "unless that is literally this department's own field), 'member_id' "
                    "(e.g. 'MBR12345'), 'provider_id', 'status' (e.g. 'DENIED'), 'date_from', 'date_to' "
                    "(ISO 'YYYY-MM-DD'). Other keys may be added freely for anything not covered above.",
                    "additionalProperties": {"type": "string"},
                },
                "suggested_render": {
                    "type": "string",
                    "enum": ["table", "chart", "text", "none"],
                    "description": "Best guess at how the eventual answer should be displayed.",
                },
            },
            "required": ["intent", "confidence", "suggested_render"],
        },
    }

RESPONSE_FINALIZATION_SYSTEM = """You are the response-synthesis stage of an internal
enterprise assistant for a nationwide health insurance company. You are given the
user's message, the classified intent, and a set of retrieved_context items
(rows already pulled from the department's own database and/or internal
policy/knowledge documents by the calling application — you did not fetch
these yourself and must not invent data beyond them). Write a clear, concise,
professional answer grounded ONLY in the supplied context. If the context is
insufficient to answer confidently, say so plainly rather than guessing. Call
the final_response tool with your result."""

RESPONSE_TOOL = {
    "name": "final_response",
    "description": "Provide the finalized answer to return to the user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer_markdown": {
                "type": "string",
                "description": "The final answer, in markdown, grounded in retrieved_context.",
            },
            "render": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["table", "chart", "text", "none"]},
                    "spec": {
                        "type": "object",
                        "description": "For 'table': {columns:[...], rows:[[...]]}. "
                        "For 'chart': {chart_type: 'bar'|'line'|'pie', labels:[...], series:[{name, values:[...]}]}.",
                    },
                },
                "required": ["type"],
            },
            "citations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Which retrieved_context item ids/titles were actually used.",
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["answer_markdown", "render"],
    },
}

HITL_DRAFT_SYSTEM = """You are the human-in-the-loop drafting stage of an internal
enterprise assistant for a nationwide health insurance company. The user wants to
create or update a record. The user message will specify an allowed_fields list —
these are the ONLY field names that exist as columns in the target table. Using
ONLY the details present in the conversation and the supplied retrieved_context,
draft the proposed record using ONLY keys from allowed_fields. NEVER invent a
field name that is not in allowed_fields, even if it seems like useful
information to capture (e.g. do not invent 'appeal_reason' or
'supporting_documentation' unless those exact names appear in allowed_fields).
If you cannot confidently populate an allowed field, you MUST leave that key
OUT of proposed_payload entirely — do not include it with a placeholder,
guess, or sentinel value of any kind (e.g. never write "<UNKNOWN>", "N/A",
"TBD", "unknown", an empty string, or a made-up date). A field is either a
real, grounded value from the conversation/retrieved_context, or it is absent
from proposed_payload — there is no third option. List every field from
required_fields that you left out this way in missing_fields — a human
reviewer will fill those gaps before this is committed, and a placeholder
value would be silently submitted as real data if the reviewer does not
notice and change it. Call the propose_record tool with your result."""

HITL_TOOL = {
    "name": "propose_record",
    "description": "Propose a record for human review before it is committed to the database.",
    "input_schema": {
        "type": "object",
        "properties": {
            "proposed_payload": {
                "type": "object",
                "description": "Field/value pairs using ONLY keys present in the allowed_fields list "
                "supplied in the user message. Do not include any key not in allowed_fields. Every "
                "value present here must be a real value you are confident in — NEVER a placeholder "
                "or sentinel like '<UNKNOWN>', 'N/A', 'TBD', or an empty string. If you cannot "
                "confidently populate a field, omit that key from this object entirely and list it "
                "in missing_fields instead — do not include the key with a fake value.",
                "additionalProperties": True,
            },
            "missing_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields FROM required_fields (never invented names) that you could not "
                "confidently populate — the human reviewer will need to fill these in before approving.",
            },
            "rationale": {
                "type": "string",
                "description": "Brief explanation of why this record is being proposed, for the reviewer.",
            },
        },
        "required": ["proposed_payload", "rationale"],
    },
}