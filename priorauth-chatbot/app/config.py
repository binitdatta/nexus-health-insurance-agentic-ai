"""
Configuration for the Prior Authorization department chatbot. Copied
from claims-chatbot per the platform pattern — see the README for what
was changed to stand this one up.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    ENV = os.getenv("FLASK_ENV", "production")
    DEBUG = _bool("FLASK_DEBUG", "false")
    SECRET_KEY = os.getenv("SECRET_KEY", "")  # signs the Flask session cookie AND the OAuth state token

    # --- This chatbot's identity ---------------------------------------
    DEPT_CODE = os.getenv("DEPT_CODE", "PRIORAUTH")
    CHATBOT_SOURCE = os.getenv("CHATBOT_SOURCE", "priorauth-chatbot")
    DEPT_DISPLAY_NAME = os.getenv("DEPT_DISPLAY_NAME", "Prior Authorization")

    # --- MySQL (health_ai_platform) -------------------------------------
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DB = os.getenv("MYSQL_DB", "health_ai_platform")
    MYSQL_POOL_SIZE = int(os.getenv("MYSQL_POOL_SIZE", "10"))
    MYSQL_CONNECT_TIMEOUT = int(os.getenv("MYSQL_CONNECT_TIMEOUT", "5"))

    # --- Keycloak 26 Central Realm (PKCE public client) ------------------
    KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "http://localhost:8080/realms/health-ai-central")
    KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "priorauth-chatbot-pkce")
    KEYCLOAK_AUTHORIZATION_ENDPOINT = os.getenv("KEYCLOAK_AUTHORIZATION_ENDPOINT", "")  # derived if blank
    KEYCLOAK_TOKEN_ENDPOINT = os.getenv("KEYCLOAK_TOKEN_ENDPOINT", "")                  # derived if blank
    KEYCLOAK_LOGOUT_ENDPOINT = os.getenv("KEYCLOAK_LOGOUT_ENDPOINT", "")                # derived if blank
    KEYCLOAK_JWKS_URL = os.getenv("KEYCLOAK_JWKS_URL", "")                              # derived if blank
    KEYCLOAK_JWKS_STATIC_FILE = os.getenv("KEYCLOAK_JWKS_STATIC_FILE", "")
    KEYCLOAK_SCOPES = os.getenv("KEYCLOAK_SCOPES", "openid profile email")
    KEYCLOAK_DEPARTMENT_CLAIM = os.getenv("KEYCLOAK_DEPARTMENT_CLAIM", "department")
    KEYCLOAK_ALGORITHMS = ["RS256"]
    KEYCLOAK_LEEWAY_SECONDS = int(os.getenv("KEYCLOAK_LEEWAY_SECONDS", "10"))
    OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5002/auth/callback")
    OAUTH_STATE_MAX_AGE_SECONDS = int(os.getenv("OAUTH_STATE_MAX_AGE_SECONDS", "300"))
    POST_LOGOUT_REDIRECT_URI = os.getenv("POST_LOGOUT_REDIRECT_URI", "http://localhost:5002/")

    # DEV ONLY: skip real Keycloak login and mint a local dev session.
    DEV_BYPASS_AUTH = _bool("DEV_BYPASS_AUTH", "false")

    # --- Central LLM Gateway ---------------------------------------------
    GATEWAY_BASE_URL = os.getenv("GATEWAY_BASE_URL", "http://localhost:8000")
    GATEWAY_TIMEOUT_SECONDS = float(os.getenv("GATEWAY_TIMEOUT_SECONDS", "35"))

    # --- Logging ------------------------------------------------------------
    LOG_DIR = os.getenv("LOG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
    LOG_FILE_NAME = os.getenv("LOG_FILE_NAME", "priorauth_chatbot.log")
    LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(25 * 1024 * 1024)))
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "10"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # --- Retrieval tuning -----------------------------------------------
    MAX_PA_ROWS = int(os.getenv("MAX_PA_ROWS", "25"))
    MAX_KNOWLEDGE_DOCS = int(os.getenv("MAX_KNOWLEDGE_DOCS", "5"))
    MAX_CONVERSATION_HISTORY_TURNS = int(os.getenv("MAX_CONVERSATION_HISTORY_TURNS", "6"))

    def derived_authorization_endpoint(self) -> str:
        return self.KEYCLOAK_AUTHORIZATION_ENDPOINT or f"{self.KEYCLOAK_ISSUER}/protocol/openid-connect/auth"

    def derived_token_endpoint(self) -> str:
        return self.KEYCLOAK_TOKEN_ENDPOINT or f"{self.KEYCLOAK_ISSUER}/protocol/openid-connect/token"

    def derived_logout_endpoint(self) -> str:
        return self.KEYCLOAK_LOGOUT_ENDPOINT or f"{self.KEYCLOAK_ISSUER}/protocol/openid-connect/logout"

    def derived_jwks_url(self) -> str:
        return self.KEYCLOAK_JWKS_URL or f"{self.KEYCLOAK_ISSUER}/protocol/openid-connect/certs"
