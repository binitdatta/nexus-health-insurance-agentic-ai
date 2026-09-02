"""
Configuration for the Central LLM Gateway.

Every setting is sourced from the environment (see .env.example) so the
same image runs unmodified across dev / staging / prod. Nothing here
performs any DDL or schema management — the MySQL schema is owned
entirely by schema.sql and this app only ever runs hand-written
INSERT/SELECT statements against it.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Flask -----------------------------------------------------
    ENV = os.getenv("FLASK_ENV", "production")
    DEBUG = _bool("FLASK_DEBUG", "false")
    JSON_SORT_KEYS = False

    # --- Anthropic ---------------------------------------------------
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    # Used for RESPONSE_FINALIZATION and HITL_DRAFT (needs stronger reasoning)
    ANTHROPIC_PRIMARY_MODEL = os.getenv("ANTHROPIC_PRIMARY_MODEL", "claude-sonnet-4-6")
    # Used for INTENT_DETECTION (cheap/fast classification)
    ANTHROPIC_FAST_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20251001")
    ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "1500"))
    ANTHROPIC_TIMEOUT_SECONDS = float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "30"))

    # Per-million-token pricing in USD. Keep this current with Anthropic's
    # published pricing (https://docs.claude.com) — it is intentionally
    # data, not code, so an operator can correct it without a deploy.
    MODEL_PRICING = {
        "claude-sonnet-4-6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
        "claude-haiku-4-5-20251001": {"input_per_mtok": 0.80, "output_per_mtok": 4.00},
        "claude-opus-4-8": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    }
    # Fallback rate applied if a model isn't in MODEL_PRICING, so cost
    # logging degrades gracefully instead of throwing.
    DEFAULT_PRICING = {"input_per_mtok": 3.00, "output_per_mtok": 15.00}

    # --- MySQL (health_ai_platform, schema owned by schema.sql) -----
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DB = os.getenv("MYSQL_DB", "health_ai_platform")
    MYSQL_POOL_SIZE = int(os.getenv("MYSQL_POOL_SIZE", "10"))
    MYSQL_CONNECT_TIMEOUT = int(os.getenv("MYSQL_CONNECT_TIMEOUT", "5"))

    # --- Keycloak 26 (Central Realm) ---------------------------------
    KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "http://localhost:8080/realms/health-ai-central")
    # Normally derived as f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs";
    # override lets ops pin a URL, and KEYCLOAK_JWKS_STATIC_FILE lets a
    # local file be used instead (air-gapped / offline / test).
    KEYCLOAK_JWKS_URL = os.getenv("KEYCLOAK_JWKS_URL", "")
    KEYCLOAK_JWKS_STATIC_FILE = os.getenv("KEYCLOAK_JWKS_STATIC_FILE", "")
    KEYCLOAK_JWKS_CACHE_TTL_SECONDS = int(os.getenv("KEYCLOAK_JWKS_CACHE_TTL_SECONDS", "3600"))
    # Expected audience claim. Configure a Keycloak client scope /
    # audience mapper on each department PKCE client so tokens minted
    # for chatbot login carry this audience for the Gateway.
    KEYCLOAK_AUDIENCE = os.getenv("KEYCLOAK_AUDIENCE", "central-llm-api")
    # Name of the token claim that carries the caller's department code
    # (added via a Keycloak protocol mapper: User Attribute "department"
    # -> token claim "department"). Compared case-insensitively against
    # each request's dept_code.
    KEYCLOAK_DEPARTMENT_CLAIM = os.getenv("KEYCLOAK_DEPARTMENT_CLAIM", "department")
    KEYCLOAK_ALGORITHMS = ["RS256"]
    KEYCLOAK_LEEWAY_SECONDS = int(os.getenv("KEYCLOAK_LEEWAY_SECONDS", "10"))

    # DEV ONLY: skips JWT validation and trusts X-Debug-* headers instead.
    # Must never be true outside a local dev box; __init__.py refuses to
    # start with this on unless FLASK_ENV=development as well.
    DEV_BYPASS_AUTH = _bool("DEV_BYPASS_AUTH", "false")

    # --- Logging -------------------------------------------------------
    LOG_DIR = os.getenv("LOG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
    LOG_FILE_NAME = os.getenv("LOG_FILE_NAME", "llm_gateway.log")
    LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(25 * 1024 * 1024)))  # 25MB
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "10"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
