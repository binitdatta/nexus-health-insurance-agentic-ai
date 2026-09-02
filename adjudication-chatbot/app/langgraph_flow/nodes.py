"""
The four nodes of the Adjudication chatbot's LangGraph pipeline. Each
node takes the running ChatState and returns a partial update
(LangGraph merges dict returns into state). Only `retrieve_node`
touches the domain tables directly — `classify_intent_node`,
`synthesize_node`, and `hitl_draft_node` all delegate the actual LLM
call to the central Gateway via gateway_client, which is what gets
logged centrally.

Note: `claim_number` here is NOT a unique key (see repository.py's
module docstring) — a claim can have multiple adjudication_records
over time. `retrieve_node`'s claim_number lookups therefore return the
*latest* adjudication for a claim, not "the" adjudication.
"""
from flask import current_app

from .. import gateway_client, repository
from .state import ChatState


def _entity(entities: dict, *keys: str) -> str | None:
    """Returns the first non-empty value found under any of the given
    keys. Defense against the LLM using a reasonable synonym (e.g.
    'claim_id' instead of 'claim_number') instead of the exact key
    name the retrieval SQL expects — the system prompt asks for exact
    key names, but a model picking its own synonym occasionally is
    cheap to tolerate here and expensive to silently get wrong (an
    adjudication lookup falling through to an unfiltered query instead
    of erroring)."""
    for key in keys:
        value = entities.get(key)
        if value:
            return value
    return None


def _dept_id() -> int:
    config = current_app.config["APP_CONFIG"]
    dept_id = repository.get_dept_id(config.DEPT_CODE)
    if dept_id is None:
        raise RuntimeError(f"Department code '{config.DEPT_CODE}' not found in departments table")
    return dept_id


def _usage_event(operation: str, gateway_result: dict) -> dict:
    usage = gateway_result.get("usage", {})
    return {"operation": operation, **usage}


def classify_intent_node(state: ChatState) -> dict:
    result = gateway_client.detect_intent(
        session_id=state["session_id"],
        message=state["message"],
        conversation_history=state.get("conversation_history"),
    )
    return {
        "intent": result.get("intent", "other"),
        "confidence": result.get("confidence", 0.0),
        "entities": result.get("entities", {}) or {},
        "suggested_render": result.get("suggested_render", "text"),
        "usage_events": state.get("usage_events", []) + [_usage_event("INTENT_DETECTION", result)],
    }


def retrieve_node(state: ChatState) -> dict:
    config = current_app.config["APP_CONFIG"]
    dept_id = _dept_id()
    intent = state.get("intent", "other")
    entities = state.get("entities", {}) or {}
    context: list[dict] = []

    claim_number = _entity(entities, "claim_number", "claim_id", "claim_no", "claimNumber")
    adjudicator_id = _entity(entities, "adjudicator_id", "adjudicatorId")

    if intent == "data_lookup":
        rows = repository.search_adjudications(
            dept_id,
            claim_number=claim_number,
            adjudicator_id=adjudicator_id,
            decision=entities.get("decision"),
            rule_applied=entities.get("rule_applied"),
            date_from=entities.get("date_from"),
            date_to=entities.get("date_to"),
            limit=config.MAX_ADJUDICATION_ROWS,
        )
        for row in rows:
            context.append({
                "source": f"adjudication:{row['adjudication_id']}:{row['claim_number']}",
                "type": "sql_row",
                "content": _format_adjudication_row(row),
            })

    elif intent == "dashboard_metric":
        rows = repository.aggregate_adjudications_by_decision(dept_id, entities.get("date_from"), entities.get("date_to"))
        summary = "; ".join(
            f"{r['decision']}: {r['record_count']} records, total adjustment ${r['total_adjustment'] or 0:,.2f}"
            for r in rows
        )
        context.append({
            "source": "adjudication_decision_aggregate", "type": "sql_aggregate",
            "content": summary or "No adjudication records match those filters.",
        })
        rule_rows = repository.aggregate_adjudications_by_rule(dept_id)
        rule_summary = "; ".join(f"{r['rule_applied']}/{r['decision']}: {r['record_count']}" for r in rule_rows)
        if rule_summary:
            context.append({
                "source": "adjudication_rule_breakdown", "type": "sql_aggregate", "content": rule_summary,
            })

    elif intent == "policy_question":
        docs = repository.search_knowledge_docs(dept_id, state["message"], limit=config.MAX_KNOWLEDGE_DOCS)
        for doc in docs:
            context.append({"source": doc["title"], "type": f"knowledge_doc:{doc['doc_type']}", "content": doc["content"]})

    elif intent in ("create_record", "update_record"):
        if claim_number:
            existing = repository.get_latest_adjudication_by_claim(dept_id, claim_number)
            if existing:
                context.append({
                    "source": f"adjudication:{existing['adjudication_id']}:{existing['claim_number']}",
                    "type": "sql_row", "content": _format_adjudication_row(existing),
                })
        docs = repository.search_knowledge_docs(dept_id, "adjudication rule engine pended claims review", limit=2)
        for doc in docs:
            context.append({"source": doc["title"], "type": f"knowledge_doc:{doc['doc_type']}", "content": doc["content"]})

    elif intent == "summarize":
        rows = repository.search_adjudications(
            dept_id, adjudicator_id=adjudicator_id, decision=entities.get("decision"),
            rule_applied=entities.get("rule_applied"), date_from=entities.get("date_from"),
            date_to=entities.get("date_to"), limit=config.MAX_ADJUDICATION_ROWS,
        )
        for row in rows:
            context.append({"source": f"adjudication:{row['adjudication_id']}:{row['claim_number']}", "type": "sql_row", "content": _format_adjudication_row(row)})

    return {"retrieved_context": context}


def synthesize_node(state: ChatState) -> dict:
    result = gateway_client.finalize_response(
        session_id=state["session_id"],
        message=state["message"],
        intent=state.get("intent", "other"),
        retrieved_context=state.get("retrieved_context", []),
        conversation_history=state.get("conversation_history"),
    )
    return {
        "answer_markdown": result.get("answer_markdown", ""),
        "render": result.get("render", {"type": "text"}),
        "citations": result.get("citations", []),
        "response_confidence": result.get("confidence", 0.0),
        "usage_events": state.get("usage_events", []) + [_usage_event("RESPONSE_FINALIZATION", result)],
    }


def hitl_draft_node(state: ChatState) -> dict:
    config = current_app.config["APP_CONFIG"]
    result = gateway_client.draft_hitl_record(
        session_id=state["session_id"],
        message=state["message"],
        entity_type="adjudication_records",
        retrieved_context=state.get("retrieved_context", []),
        allowed_fields=repository.ADJUDICATION_COLUMNS,
        required_fields=repository.REQUIRED_ADJUDICATION_FIELDS,
        conversation_history=state.get("conversation_history"),
    )
    task_id = repository.insert_hitl_task(
        dept_id=_dept_id(),
        chatbot_source=config.CHATBOT_SOURCE,
        session_id=state["session_id"],
        requested_by_user_id=state.get("requested_by_user_id"),
        entity_type="adjudication_records",
        proposed_payload=result.get("proposed_payload", {}),
        ai_rationale=result.get("rationale", ""),
    )
    return {
        "hitl": {
            "task_id": task_id,
            "proposed_payload": result.get("proposed_payload", {}),
            "missing_fields": result.get("missing_fields", []),
            "rationale": result.get("rationale", ""),
        },
        "usage_events": state.get("usage_events", []) + [_usage_event("HITL_DRAFT", result)],
    }


def _format_adjudication_row(row: dict) -> str:
    parts = [f"{k}={v}" for k, v in row.items() if v is not None]
    return ", ".join(parts)
