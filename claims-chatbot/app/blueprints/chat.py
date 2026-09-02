import uuid

from flask import Blueprint, current_app, jsonify, render_template, request, session

from .. import repository
from ..gateway_client import GatewayError
from ..langgraph_flow.graph import run_chat_turn
from ..security.decorators import current_user, login_required

chat_bp = Blueprint("chat", __name__)


@chat_bp.get("/")
@login_required
def index():
    config = current_app.config["APP_CONFIG"]
    if "chat_session_id" not in session:
        session["chat_session_id"] = str(uuid.uuid4())
        session["chat_history"] = []
    return render_template("chat.html", config=config, user=current_user())


@chat_bp.post("/api/chat")
@login_required
def api_chat():
    config = current_app.config["APP_CONFIG"]
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify(error="message is required"), 400

    if "chat_session_id" not in session:
        session["chat_session_id"] = str(uuid.uuid4())
    session_id = session["chat_session_id"]
    history = session.get("chat_history", [])

    user = current_user()
    dept_id = repository.get_dept_id(config.DEPT_CODE)
    requested_by_user_id = None
    if dept_id is not None:
        try:
            requested_by_user_id = repository.get_or_create_user(
                user["sub"], dept_id, user.get("username", ""), user.get("email", "")
            )
        except Exception:  # noqa: BLE001
            requested_by_user_id = None

    try:
        final_state = run_chat_turn(
            session_id=session_id,
            message=message,
            conversation_history=history[-(config.MAX_CONVERSATION_HISTORY_TURNS * 2):],
            requested_by_user_id=requested_by_user_id,
        )
    except GatewayError as exc:
        return jsonify(error=exc.message), exc.status_code

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": final_state.get("answer_markdown", "")})
    session["chat_history"] = history[-(config.MAX_CONVERSATION_HISTORY_TURNS * 2):]

    total_cost = sum(e.get("total_cost_usd", 0) or 0 for e in final_state.get("usage_events", []))
    total_tokens = sum((e.get("prompt_tokens", 0) or 0) + (e.get("completion_tokens", 0) or 0) for e in final_state.get("usage_events", []))

    return jsonify(
        intent=final_state.get("intent"),
        answer_markdown=final_state.get("answer_markdown", ""),
        render=final_state.get("render", {"type": "text"}),
        citations=final_state.get("citations", []),
        hitl=final_state.get("hitl"),
        usage_events=final_state.get("usage_events", []),
        turn_cost_usd=round(total_cost, 6),
        turn_tokens=total_tokens,
    ), 200
