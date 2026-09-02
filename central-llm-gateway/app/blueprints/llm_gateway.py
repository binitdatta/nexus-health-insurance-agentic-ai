"""
The three endpoints every department chatbot calls instead of talking
to Anthropic directly:

  POST /api/v1/llm/intent    - classify what the user wants
  POST /api/v1/llm/respond   - synthesize the final answer from
                                caller-supplied retrieved_context
  POST /api/v1/llm/hitl-draft - draft a proposed record for human review

Retrieval itself (SQL against the department's domain tables, RAG
lookups against knowledge_docs) is each chatbot's own responsibility,
typically as a LangGraph node — this Gateway only ever does the LLM
call, and centrally logs it.
"""
import uuid

from flask import Blueprint, current_app, g, jsonify, request

from .. import repository
from ..anthropic_client import AnthropicCallError, AnthropicGatewayClient
from ..auth import require_department_auth
from ..logging_utils import log_error, log_llm_call

llm_bp = Blueprint("llm_gateway", __name__, url_prefix="/api/v1/llm")


def _client() -> AnthropicGatewayClient:
    # Cached on the app object so we reuse one anthropic.Anthropic
    # instance (and its connection pool) per process.
    if "anthropic_client" not in current_app.extensions:
        current_app.extensions["anthropic_client"] = AnthropicGatewayClient(current_app.config["APP_CONFIG"])
    return current_app.extensions["anthropic_client"]


def _require_fields(body: dict, fields: list[str]):
    missing = [f for f in fields if not body.get(f)]
    if missing:
        return f"Missing required field(s): {', '.join(missing)}"
    return None


def _persist(*, request_id, dept_code, chatbot_source, session_id, llm_result, http_status, call_status,
             error_message=None, intent_detected=None):
    config = current_app.config["APP_CONFIG"]
    logger = current_app.extensions["llm_logger"]

    dept_id = repository.get_dept_id(dept_code)
    user_id = None
    if dept_id is not None:
        auth = getattr(g, "auth", {}) or {}
        keycloak_sub = auth.get("sub", "unknown")
        username = auth.get("preferred_username", "")
        email = auth.get("email", "")
        try:
            user_id = repository.get_or_create_user(keycloak_sub, dept_id, username, email)
        except Exception:  # noqa: BLE001 - never let user provisioning break the response
            user_id = None

    entry = {
        "request_id": request_id,
        "dept_id": dept_id,
        "user_id": user_id,
        "chatbot_source": chatbot_source,
        "session_id": session_id,
        "model_name": llm_result.model if llm_result else "n/a",
        "operation": llm_result.operation if llm_result else "UNKNOWN",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "intent_detected": intent_detected,
        "request_payload": llm_result.raw_request if llm_result else {},
        "response_payload": {"result": llm_result.result, **llm_result.raw_response_summary} if llm_result else {"error": error_message},
        "prompt_tokens": llm_result.prompt_tokens if llm_result else 0,
        "completion_tokens": llm_result.completion_tokens if llm_result else 0,
        "total_tokens": llm_result.total_tokens if llm_result else 0,
        "input_cost_usd": llm_result.input_cost_usd if llm_result else 0,
        "output_cost_usd": llm_result.output_cost_usd if llm_result else 0,
        "total_cost_usd": llm_result.total_cost_usd if llm_result else 0,
        "latency_ms": llm_result.latency_ms if llm_result else None,
        "http_status": http_status,
        "call_status": call_status,
        "error_message": error_message,
    }

    log_llm_call(logger, **entry)
    try:
        repository.insert_llm_call_log(entry)
    except Exception as exc:  # noqa: BLE001 - the flat file log above already captured this call
        log_error(logger, message="Failed to write llm_call_log to MySQL", request_id=request_id, db_error=str(exc))

    return entry


@llm_bp.post("/intent")
@require_department_auth
def intent():
    body = request.get_json(silent=True) or {}
    err = _require_fields(body, ["dept_code", "chatbot_source", "session_id", "message"])
    if err:
        return jsonify(error=err), 400

    request_id = str(uuid.uuid4())
    try:
        llm_result = _client().detect_intent(
            message=body["message"],
            department=body["dept_code"],
            conversation_history=body.get("conversation_history"),
        )
    except AnthropicCallError as exc:
        _persist(
            request_id=request_id, dept_code=body["dept_code"], chatbot_source=body["chatbot_source"],
            session_id=body["session_id"], llm_result=None, http_status=exc.status_code,
            call_status="ERROR", error_message=exc.message,
        )
        return jsonify(error=exc.message, request_id=request_id), exc.status_code

    entry = _persist(
        request_id=request_id, dept_code=body["dept_code"], chatbot_source=body["chatbot_source"],
        session_id=body["session_id"], llm_result=llm_result, http_status=200, call_status="SUCCESS",
        intent_detected=llm_result.result.get("intent"),
    )
    return jsonify(
        request_id=request_id,
        intent=llm_result.result.get("intent"),
        confidence=llm_result.result.get("confidence"),
        entities=llm_result.result.get("entities", {}),
        suggested_render=llm_result.result.get("suggested_render"),
        usage={
            "model": llm_result.model,
            "prompt_tokens": llm_result.prompt_tokens,
            "completion_tokens": llm_result.completion_tokens,
            "total_cost_usd": llm_result.total_cost_usd,
            "latency_ms": llm_result.latency_ms,
        },
    ), 200


@llm_bp.post("/respond")
@require_department_auth
def respond():
    body = request.get_json(silent=True) or {}
    err = _require_fields(body, ["dept_code", "chatbot_source", "session_id", "message", "intent"])
    if err:
        return jsonify(error=err), 400

    request_id = str(uuid.uuid4())
    try:
        llm_result = _client().finalize_response(
            message=body["message"],
            department=body["dept_code"],
            intent=body["intent"],
            retrieved_context=body.get("retrieved_context", []),
            conversation_history=body.get("conversation_history"),
        )
    except AnthropicCallError as exc:
        _persist(
            request_id=request_id, dept_code=body["dept_code"], chatbot_source=body["chatbot_source"],
            session_id=body["session_id"], llm_result=None, http_status=exc.status_code,
            call_status="ERROR", error_message=exc.message, intent_detected=body.get("intent"),
        )
        return jsonify(error=exc.message, request_id=request_id), exc.status_code

    _persist(
        request_id=request_id, dept_code=body["dept_code"], chatbot_source=body["chatbot_source"],
        session_id=body["session_id"], llm_result=llm_result, http_status=200, call_status="SUCCESS",
        intent_detected=body.get("intent"),
    )
    return jsonify(
        request_id=request_id,
        answer_markdown=llm_result.result.get("answer_markdown"),
        render=llm_result.result.get("render", {"type": "text"}),
        citations=llm_result.result.get("citations", []),
        confidence=llm_result.result.get("confidence"),
        usage={
            "model": llm_result.model,
            "prompt_tokens": llm_result.prompt_tokens,
            "completion_tokens": llm_result.completion_tokens,
            "total_cost_usd": llm_result.total_cost_usd,
            "latency_ms": llm_result.latency_ms,
        },
    ), 200


@llm_bp.post("/hitl-draft")
@require_department_auth
def hitl_draft():
    body = request.get_json(silent=True) or {}
    err = _require_fields(body, ["dept_code", "chatbot_source", "session_id", "message", "entity_type", "allowed_fields"])
    if err:
        return jsonify(error=err), 400

    request_id = str(uuid.uuid4())
    try:
        llm_result = _client().draft_hitl_record(
            message=body["message"],
            department=body["dept_code"],
            entity_type=body["entity_type"],
            retrieved_context=body.get("retrieved_context", []),
            allowed_fields=body["allowed_fields"],
            required_fields=body.get("required_fields"),
            conversation_history=body.get("conversation_history"),
        )
    except AnthropicCallError as exc:
        _persist(
            request_id=request_id, dept_code=body["dept_code"], chatbot_source=body["chatbot_source"],
            session_id=body["session_id"], llm_result=None, http_status=exc.status_code,
            call_status="ERROR", error_message=exc.message,
        )
        return jsonify(error=exc.message, request_id=request_id), exc.status_code

    _persist(
        request_id=request_id, dept_code=body["dept_code"], chatbot_source=body["chatbot_source"],
        session_id=body["session_id"], llm_result=llm_result, http_status=200, call_status="SUCCESS",
        intent_detected="create_record",
    )
    return jsonify(
        request_id=request_id,
        proposed_payload=llm_result.result.get("proposed_payload", {}),
        missing_fields=llm_result.result.get("missing_fields", []),
        rationale=llm_result.result.get("rationale"),
        usage={
            "model": llm_result.model,
            "prompt_tokens": llm_result.prompt_tokens,
            "completion_tokens": llm_result.completion_tokens,
            "total_cost_usd": llm_result.total_cost_usd,
            "latency_ms": llm_result.latency_ms,
        },
    ), 200