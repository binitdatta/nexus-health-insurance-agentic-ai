import decimal
import time
import uuid
from datetime import date, datetime

from flask import Flask, g, jsonify, request
from flask.json.provider import DefaultJSONProvider

from . import extensions
from .config import Config


class _AppJSONProvider(DefaultJSONProvider):
    """MySQL rows come back with Decimal/date/datetime values (claim
    amounts, service dates, timestamps) that Flask's default JSON
    provider can't serialize — this teaches it how, once, instead of
    hand-converting every query result before jsonify()."""

    @staticmethod
    def default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    config = config_object()
    app.config["APP_CONFIG"] = config
    app.config["DEBUG"] = config.DEBUG
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.ENV == "production"
    app.json = _AppJSONProvider(app)

    if not config.SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set — it signs both the session cookie and the OAuth state token")
    if config.DEV_BYPASS_AUTH and config.ENV != "development":
        raise RuntimeError("Refusing to start: DEV_BYPASS_AUTH=true requires FLASK_ENV=development")

    extensions.init_pool(config)
    app.extensions["chat_logger"] = extensions.init_app_logger(config)

    from .blueprints.auth_routes import auth_bp
    from .blueprints.chat import chat_bp
    from .blueprints.dashboard import dashboard_bp
    from .blueprints.hitl import hitl_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(hitl_bp)
    app.register_blueprint(dashboard_bp)

    @app.before_request
    def _start_timer():
        g._req_started = time.monotonic()
        g._req_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))

    @app.after_request
    def _add_request_id_header(response):
        response.headers["X-Request-Id"] = getattr(g, "_req_id", "")
        return response

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        return "Not found", 404

    @app.errorhandler(500)
    def internal_error(e):
        app.extensions["chat_logger"].error(f'{{"log_type": "unhandled_error", "error": "{e}"}}')
        if request.path.startswith("/api/"):
            return jsonify(error="Internal server error"), 500
        return "Internal server error", 500

    @app.context_processor
    def inject_globals():
        return {"dept_display_name": config.DEPT_DISPLAY_NAME}

    return app
