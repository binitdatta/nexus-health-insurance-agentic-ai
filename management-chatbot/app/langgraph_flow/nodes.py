"""
The four nodes of the Management chatbot's LangGraph pipeline. Each
node takes the running ChatState and returns a partial update
(LangGraph merges dict returns into state). Only `retrieve_node`
touches the domain tables directly — `classify_intent_node`,
`synthesize_node`, and `hitl_draft_node` all delegate the actual LLM
call to the central Gateway via gateway_client, which is what gets
logged centrally.

Two things are genuinely new here versus every other department
chatbot: `management_reports` is the first table whose data
cross-references OTHER departments (`covers_dept_id`), and its
`kpi_summary` column is JSON text rather than a plain scalar. Both are
handled explicitly below and in repository.py rather than silently
reusing the generic pattern.
"""
from flask import current_app

from .. import gateway_client, repository
from .state import ChatState


def _entity(entities: dict, *keys: str) -> str | None:
    """Returns the first non-empty value found under any of the given
    keys. Defense against the LLM using a reasonable synonym (e.g.
    'report_id' instead of 'report_ref') instead of the exact key name
    the retrieval SQL expects — the system prompt asks for exact key
    names, but a model picking its own synonym occasionally is cheap
    to tolerate here and expensive to silently get wrong (a report
    lookup falling through to an unfiltered query instead of
    erroring)."""
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


def _resolve_covers_dept_id(entities: dict) -> int | None:
    """The user (or the LLM's entity extraction) is far more likely to
    say a department NAME/CODE ("claims", "the Nursing department")
    than the internal numeric covers_dept_id the column stores.
    Resolves either form to the real dept_id, or None if unresolvable
    — callers fall back to an unfiltered query rather than erroring."""
    raw = _entity(entities, "covers_dept_id", "covers_dept_code", "department", "dept_code")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return repository.get_dept_id(str(raw))


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

    report_ref = _entity(entities, "report_ref", "report_id", "reportReference")
    covers_dept_id = _resolve_covers_dept_id(entities)

    if intent == "data_lookup":
        rows = repository.search_reports(
            dept_id,
            report_ref=report_ref,
            covers_dept_id=covers_dept_id,
            report_period=entities.get("report_period"),
            date_from=entities.get("date_from"),
            date_to=entities.get("date_to"),
            limit=config.MAX_REPORT_ROWS,
        )
        for row in rows:
            context.append({
                "source": f"report:{row['report_ref']}",
                "type": "sql_row",
                "content": _format_report_row(row),
            })

    elif intent == "dashboard_metric":
        rows = repository.aggregate_reports_by_covered_department(dept_id)
        summary = "; ".join(
            f"{r['dept_name']} ({r['dept_code']}): {r['report_count']} reports, avg SLA {r['avg_sla_pct']}%"
            for r in rows
        )
        context.append({
            "source": "reports_by_covered_department", "type": "sql_aggregate",
            "content": summary or "No management reports match those filters.",
        })

    elif intent == "policy_question":
        docs = repository.search_knowledge_docs(dept_id, state["message"], limit=config.MAX_KNOWLEDGE_DOCS)
        for doc in docs:
            context.append({"source": doc["title"], "type": f"knowledge_doc:{doc['doc_type']}", "content": doc["content"]})

    elif intent in ("create_record", "update_record"):
        if report_ref:
            existing = repository.get_report_by_ref(dept_id, report_ref)
            if existing:
                context.append({"source": f"report:{existing['report_ref']}", "type": "sql_row", "content": _format_report_row(existing)})
        # covers_dept_id is an internal numeric FK no one can reasonably
        # guess from a department name alone — always ground drafting in
        # the real department list so the LLM (or a human editing the
        # HITL form) has the actual code-to-id mapping in front of it.
        dept_ref_rows = repository.list_department_reference()
        dept_ref_summary = "; ".join(f"{r['dept_code']}={r['dept_id']} ({r['dept_name']})" for r in dept_ref_rows)
        context.append({
            "source": "department_reference", "type": "reference_data",
            "content": f"Valid covers_dept_id values: {dept_ref_summary}",
        })
        docs = repository.search_knowledge_docs(dept_id, "KPI definitions reporting calendar", limit=2)
        for doc in docs:
            context.append({"source": doc["title"], "type": f"knowledge_doc:{doc['doc_type']}", "content": doc["content"]})

    elif intent == "summarize":
        rows = repository.search_reports(
            dept_id, covers_dept_id=covers_dept_id, report_period=entities.get("report_period"),
            date_from=entities.get("date_from"), date_to=entities.get("date_to"), limit=config.MAX_REPORT_ROWS,
        )
        for row in rows:
            context.append({"source": f"report:{row['report_ref']}", "type": "sql_row", "content": _format_report_row(row)})

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
        entity_type="management_reports",
        retrieved_context=state.get("retrieved_context", []),
        allowed_fields=repository.REPORT_COLUMNS,
        required_fields=repository.REQUIRED_REPORT_FIELDS,
        conversation_history=state.get("conversation_history"),
    )
    task_id = repository.insert_hitl_task(
        dept_id=_dept_id(),
        chatbot_source=config.CHATBOT_SOURCE,
        session_id=state["session_id"],
        requested_by_user_id=state.get("requested_by_user_id"),
        entity_type="management_reports",
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


def _format_report_row(row: dict) -> str:
    parts = [f"{k}={v}" for k, v in row.items() if v is not None]
    return ", ".join(parts)
