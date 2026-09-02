from flask import Blueprint, current_app, jsonify

from .. import extensions

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    db_ok = True
    db_error = None
    try:
        conn = extensions.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)

    config = current_app.config["APP_CONFIG"]
    body = {
        "status": "ok" if db_ok else "degraded",
        "service": "central-llm-gateway",
        "db": "ok" if db_ok else "error",
        "dev_bypass_auth": config.DEV_BYPASS_AUTH,
    }
    if db_error:
        body["db_error"] = db_error
    return jsonify(body), 200 if db_ok else 503
