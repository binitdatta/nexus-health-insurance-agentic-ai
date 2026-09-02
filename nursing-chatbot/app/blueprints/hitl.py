from flask import Blueprint, current_app, jsonify, render_template, request

from .. import repository
from ..repository import HitlValidationError
from ..security.decorators import current_user, login_required

hitl_bp = Blueprint("hitl", __name__, url_prefix="/hitl")


def _reviewer_user_id():
    config = current_app.config["APP_CONFIG"]
    user = current_user()
    dept_id = repository.get_dept_id(config.DEPT_CODE)
    if dept_id is None or not user:
        return None
    try:
        return repository.get_or_create_user(user["sub"], dept_id, user.get("username", ""), user.get("email", ""))
    except Exception:  # noqa: BLE001
        return None


@hitl_bp.get("")
@login_required
def queue():
    config = current_app.config["APP_CONFIG"]
    dept_id = repository.get_dept_id(config.DEPT_CODE)
    status_filter = request.args.get("status", "PENDING").upper()
    status_filter = status_filter if status_filter != "ALL" else None
    tasks = repository.list_hitl_tasks(dept_id, status=status_filter, limit=100) if dept_id else []
    return render_template("hitl_queue.html", config=config, user=current_user(), tasks=tasks, status_filter=request.args.get("status", "PENDING"))


@hitl_bp.get("/api/tasks")
@login_required
def api_list_tasks():
    config = current_app.config["APP_CONFIG"]
    dept_id = repository.get_dept_id(config.DEPT_CODE)
    status_filter = request.args.get("status", "PENDING").upper()
    status_filter = status_filter if status_filter != "ALL" else None
    tasks = repository.list_hitl_tasks(dept_id, status=status_filter, limit=100) if dept_id else []
    return jsonify(tasks=tasks)


@hitl_bp.post("/api/tasks/<int:task_id>/approve")
@login_required
def api_approve(task_id: int):
    body = request.get_json(silent=True) or {}
    edited_payload = body.get("edited_payload")
    review_notes = body.get("review_notes")
    try:
        result = repository.approve_hitl_task(task_id, _reviewer_user_id(), edited_payload, review_notes)
    except HitlValidationError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(result), 200


@hitl_bp.post("/api/tasks/<int:task_id>/reject")
@login_required
def api_reject(task_id: int):
    body = request.get_json(silent=True) or {}
    review_notes = body.get("review_notes")
    try:
        repository.reject_hitl_task(task_id, _reviewer_user_id(), review_notes)
    except HitlValidationError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(status="REJECTED"), 200
