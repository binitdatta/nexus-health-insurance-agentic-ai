from flask import Flask

from .config import Config
from . import content


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    config = config_object()
    app.config["APP_CONFIG"] = config
    app.config["DEBUG"] = config.DEBUG
    app.config["SECRET_KEY"] = config.SECRET_KEY

    from .routes import hub_bp
    app.register_blueprint(hub_bp)

    @app.context_processor
    def inject_globals():
        # Available in every template without passing them explicitly
        # from every view function — this is what keeps the navbar's
        # Chatbots and Training dropdowns in sync with app/content.py
        # automatically.
        return {
            "nav_chatbots": content.CHATBOTS,
            "nav_training_topics": content.TRAINING_TOPICS,
            "hub_host": config.HUB_HOST,
        }

    @app.errorhandler(404)
    def not_found(_e):
        return "Not found", 404

    return app
