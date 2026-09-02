"""
Everything this chatbot needs from the central LLM Gateway. Never calls
Anthropic directly — every LLM interaction goes through the Gateway's
three operations, and every one of those HTTP calls is logged here
(flat file + http_call_log), separately from the Gateway's own
llm_call_log of the underlying Anthropic call.
"""
import time
import uuid

import requests
from flask import current_app, session

from . import repository
from .logging_utils import log_http_call


class GatewayError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _auth_headers(config) -> dict:
    if config.DEV_BYPASS_AUTH:
        user = session.get("user", {})
        return {
            "X-Debug-Department": config.DEPT_CODE,
            "X-Debug-User": user.get("sub", "dev-user-sub"),
            "X-Debug-Roles": ",".join(user.get("roles", [])),
        }
    token = session.get("access_token")
    if not token:
        raise GatewayError("No access token in session — please log in again", 401)
    return {"Authorization": f"Bearer {token}"}


def _post(path: str, payload: dict) -> dict:
    config = current_app.config["APP_CONFIG"]
    logger = current_app.extensions["chat_logger"]

    url = config.GATEWAY_BASE_URL.rstrip("/") + path
    request_id = str(uuid.uuid4())
    headers = {"Content-Type": "application/json", "X-Request-Id": request_id}
    try:
        headers.update(_auth_headers(config))
    except GatewayError:
        raise

    dept_id = repository.get_dept_id(config.DEPT_CODE)
    user = session.get("user", {})
    user_id = None
    if dept_id is not None and user.get("sub"):
        try:
            user_id = repository.get_or_create_user(user["sub"], dept_id, user.get("username", ""), user.get("email", ""))
        except Exception:  # noqa: BLE001
            user_id = None

    started = time.monotonic()
    status_code = None
    response_body = None
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=config.GATEWAY_TIMEOUT_SECONDS)
        status_code = resp.status_code
        response_body = resp.json() if resp.content else {}
    except requests.RequestException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        _log(logger, dept_id, user_id, config, request_id, "POST", path, payload, {"error": str(exc)}, None, latency_ms)
        raise GatewayError(f"Gateway request failed: {exc}", 502)

    latency_ms = int((time.monotonic() - started) * 1000)
    _log(logger, dept_id, user_id, config, request_id, "POST", path, payload, response_body, status_code, latency_ms)

    if status_code != 200:
        raise GatewayError(response_body.get("error", f"Gateway returned {status_code}"), status_code)
    return response_body


def _log(logger, dept_id, user_id, config, request_id, method, path, req_body, resp_body, status_code, latency_ms):
    entry = {
        "request_id": request_id,
        "dept_id": dept_id,
        "user_id": user_id,
        "chatbot_source": config.CHATBOT_SOURCE,
        "session_id": session.get("chat_session_id", "unknown"),
        "http_method": method,
        "endpoint": path,
        "target_service": "central-llm-api",
        "request_payload": req_body,
        "response_payload": resp_body,
        "response_status": status_code,
        "latency_ms": latency_ms,
        "client_ip": None,
    }
    log_http_call(logger, **entry)
    try:
        repository.insert_http_call_log(entry)
    except Exception as exc:  # noqa: BLE001 - flat file already has it
        logger.error(f'{{"log_type":"error","message":"Failed to write http_call_log","db_error":"{exc}"}}')


def detect_intent(*, session_id: str, message: str, conversation_history: list = None) -> dict:
    config = current_app.config["APP_CONFIG"]
    return _post("/api/v1/llm/intent", {
        "dept_code": config.DEPT_CODE,
        "chatbot_source": config.CHATBOT_SOURCE,
        "session_id": session_id,
        "message": message,
        "conversation_history": conversation_history or [],
    })


def finalize_response(*, session_id: str, message: str, intent: str, retrieved_context: list,
                       conversation_history: list = None) -> dict:
    config = current_app.config["APP_CONFIG"]
    return _post("/api/v1/llm/respond", {
        "dept_code": config.DEPT_CODE,
        "chatbot_source": config.CHATBOT_SOURCE,
        "session_id": session_id,
        "message": message,
        "intent": intent,
        "retrieved_context": retrieved_context,
        "conversation_history": conversation_history or [],
    })


def draft_hitl_record(*, session_id: str, message: str, entity_type: str, retrieved_context: list,
                       allowed_fields: list, required_fields: list = None,
                       conversation_history: list = None) -> dict:
    config = current_app.config["APP_CONFIG"]
    return _post("/api/v1/llm/hitl-draft", {
        "dept_code": config.DEPT_CODE,
        "chatbot_source": config.CHATBOT_SOURCE,
        "session_id": session_id,
        "message": message,
        "entity_type": entity_type,
        "retrieved_context": retrieved_context,
        "allowed_fields": allowed_fields,
        "required_fields": required_fields or [],
        "conversation_history": conversation_history or [],
    })
