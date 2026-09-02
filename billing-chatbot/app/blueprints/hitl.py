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
    except Exception as exc:  # noqa: BLE001
        # Last-resort safety net: a bad value in any editable field — an AI
        # draft that didn't fully follow its own "omit ungroundable fields"
        # instruction (e.g. filled a date with a literal "<UNKNOWN>" instead
        # of leaving the key out), or simply a human reviewer typo — can
        # reach the database as an invalid literal for that column's type.
        # Without this, that raises an unhandled exception, Flask returns
        # its default HTML 500 page, and the frontend's `resp.json()` call
        # throws a confusing SyntaxError instead of ever showing the
        # reviewer what actually went wrong.
        current_app.logger.exception("HITL approve failed for task %s", task_id)
        return jsonify(error="Could not save this record — check that every field has a valid "
                              "value for its type (e.g. dates must be YYYY-MM-DD, not a placeholder "
                              f"like '<UNKNOWN>'). Details: {exc}"), 400
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
    except Exception as exc:  # noqa: BLE001 — same safety net as api_approve above
        current_app.logger.exception("HITL reject failed for task %s", task_id)
        return jsonify(error=f"Could not reject this task. Details: {exc}"), 400
    return jsonify(status="REJECTED"), 200