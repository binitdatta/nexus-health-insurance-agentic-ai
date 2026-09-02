from flask import Blueprint, current_app, jsonify, render_template

from .. import repository
from ..security.decorators import current_user, login_required

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.get("")
@login_required
def index():
    config = current_app.config["APP_CONFIG"]
    return render_template("dashboard.html", config=config, user=current_user())


@dashboard_bp.get("/api/summary")
@login_required
def api_summary():
    config = current_app.config["APP_CONFIG"]
    dept_id = repository.get_dept_id(config.DEPT_CODE)
    if dept_id is None:
        return jsonify(error="Department not found"), 500

    daily = repository.dashboard_daily_summary(dept_id, config.CHATBOT_SOURCE, days=14)
    totals = repository.dashboard_totals(dept_id, config.CHATBOT_SOURCE)
    recent_llm = repository.dashboard_recent_llm_calls(dept_id, config.CHATBOT_SOURCE, limit=20)
    recent_http = repository.dashboard_recent_http_calls(dept_id, config.CHATBOT_SOURCE, limit=20)

    return jsonify(
        daily=daily,
        totals=totals,
        recent_llm_calls=recent_llm,
        recent_http_calls=recent_http,
    )
