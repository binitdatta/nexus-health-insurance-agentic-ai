"""
The four nodes of the Nursing chatbot's LangGraph pipeline. Each node
takes the running ChatState and returns a partial update (LangGraph
merges dict returns into state). Only `retrieve_node` touches the
domain tables directly — `classify_intent_node`, `synthesize_node`, and
`hitl_draft_node` all delegate the actual LLM call to the central
Gateway via gateway_client, which is what gets logged centrally.
"""
from flask import current_app

from .. import gateway_client, repository
from .state import ChatState


def _entity(entities: dict, *keys: str) -> str | None:
    """Returns the first non-empty value found under any of the given
    keys. Defense against the LLM using a reasonable synonym (e.g.
    'case_id' instead of 'case_number') instead of the exact key name
    the retrieval SQL expects — the system prompt asks for exact key
    names, but a model picking its own synonym occasionally is cheap
    to tolerate here and expensive to silently get wrong (a case
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

    case_number = _entity(entities, "case_number", "case_id", "case_no", "caseNumber")
    member_id = _entity(entities, "member_id", "memberId", "member_no")

    if intent == "data_lookup":
        rows = repository.search_nursing_cases(
            dept_id,
            case_number=case_number,
            member_id=member_id,
            status=entities.get("status"),
            case_type=entities.get("case_type"),
            acuity_level=entities.get("acuity_level") or entities.get("acuity"),
            date_from=entities.get("date_from"),
            date_to=entities.get("date_to"),
            limit=config.MAX_NURSING_ROWS,
        )
        for row in rows:
            context.append({
                "source": f"nursing_case:{row['case_number']}",
                "type": "sql_row",
                "content": _format_case_row(row),
            })

    elif intent == "dashboard_metric":
        rows = repository.aggregate_cases_by_status(dept_id, entities.get("date_from"), entities.get("date_to"))
        summary = "; ".join(f"{r['status']}: {r['case_count']} cases" for r in rows)
        context.append({
            "source": "nursing_status_aggregate", "type": "sql_aggregate",
            "content": summary or "No nursing cases match those filters.",
        })
        acuity_rows = repository.aggregate_cases_by_acuity(dept_id)
        acuity_summary = "; ".join(f"{r['acuity_level']}/{r['status']}: {r['case_count']}" for r in acuity_rows)
        if acuity_summary:
            context.append({
                "source": "nursing_acuity_breakdown", "type": "sql_aggregate", "content": acuity_summary,
            })

    elif intent == "policy_question":
        docs = repository.search_knowledge_docs(dept_id, state["message"], limit=config.MAX_KNOWLEDGE_DOCS)
        for doc in docs:
            context.append({"source": doc["title"], "type": f"knowledge_doc:{doc['doc_type']}", "content": doc["content"]})

    elif intent in ("create_record", "update_record"):
        if case_number:
            existing = repository.get_case_by_number(dept_id, case_number)
            if existing:
                context.append({"source": f"nursing_case:{existing['case_number']}", "type": "sql_row", "content": _format_case_row(existing)})
        docs = repository.search_knowledge_docs(dept_id, "care management acuity discharge planning", limit=2)
        for doc in docs:
            context.append({"source": doc["title"], "type": f"knowledge_doc:{doc['doc_type']}", "content": doc["content"]})

    elif intent == "summarize":
        rows = repository.search_nursing_cases(
            dept_id, member_id=member_id, status=entities.get("status"), case_type=entities.get("case_type"),
            acuity_level=entities.get("acuity_level") or entities.get("acuity"),
            date_from=entities.get("date_from"), date_to=entities.get("date_to"), limit=config.MAX_NURSING_ROWS,
        )
        for row in rows:
            context.append({"source": f"nursing_case:{row['case_number']}", "type": "sql_row", "content": _format_case_row(row)})

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
        entity_type="nursing_cases",
        retrieved_context=state.get("retrieved_context", []),
        allowed_fields=repository.NURSING_CASE_COLUMNS,
        required_fields=repository.REQUIRED_NURSING_CASE_FIELDS,
        conversation_history=state.get("conversation_history"),
    )
    task_id = repository.insert_hitl_task(
        dept_id=_dept_id(),
        chatbot_source=config.CHATBOT_SOURCE,
        session_id=state["session_id"],
        requested_by_user_id=state.get("requested_by_user_id"),
        entity_type="nursing_cases",
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


def _format_case_row(row: dict) -> str:
    parts = [f"{k}={v}" for k, v in row.items() if v is not None]
    return ", ".join(parts)
