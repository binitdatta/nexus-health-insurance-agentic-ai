import time
import uuid

from flask import Flask, g, jsonify, request

from .config import Config
from . import extensions


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    config = config_object()
    app.config["APP_CONFIG"] = config
    app.config["DEBUG"] = config.DEBUG

    if config.DEV_BYPASS_AUTH and config.ENV != "development":
        raise RuntimeError("Refusing to start: DEV_BYPASS_AUTH=true requires FLASK_ENV=development")

    extensions.init_pool(config)
    app.extensions["llm_logger"] = extensions.init_app_logger(config)

    from .blueprints.health import health_bp
    from .blueprints.llm_gateway import llm_bp

    app.register_blueprint(health_bp, url_prefix="/api/v1")
    app.register_blueprint(llm_bp)

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
        return jsonify(error="Not found"), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify(error="Method not allowed"), 405

    @app.errorhandler(500)
    def internal_error(e):
        app.extensions["llm_logger"].error(f'{{"log_type": "unhandled_error", "error": "{e}"}}')
        return jsonify(error="Internal server error"), 500

    return app
