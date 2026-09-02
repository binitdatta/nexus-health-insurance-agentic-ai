"""
Keycloak 26 Central Realm token validation.

Architecture: each department chatbot UI is a PKCE *public* client in the
Central Realm. The end user authenticates there, and the chatbot's own
Flask backend relays that same user access token in the Authorization
header when it calls this Gateway (token relay — no separate service
credential to manage, and RBAC stays anchored to the actual department
user, per the requirement that "department users drive RBAC").

This module verifies:
  1. The token is a validly signed, unexpired RS256 JWT from the
     configured Keycloak issuer.
  2. The audience claim includes this Gateway (KEYCLOAK_AUDIENCE) —
     set up via a client scope / audience mapper on each PKCE client.
  3. The token's department claim (KEYCLOAK_DEPARTMENT_CLAIM, default
     "department", populated via a Keycloak User Attribute -> token
     claim protocol mapper) matches the dept_code the caller is
     requesting on behalf of. This is what stops the Claims chatbot's
     token from being replayed against a Billing endpoint.

Setup note for the Keycloak admin: on the Central Realm,
  - add a User Attribute "department" to each department user, and a
    protocol mapper (User Attribute -> Token Claim "department") on the
    Central Realm's default client scope so it lands in every access
    token.
  - add an Audience mapper to each department's PKCE client
    (claims-chatbot-pkce, priorauth-chatbot-pkce, ...) that adds
    "central-llm-api" as an audience.
"""
import json
import os
import time
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request
from jwt import PyJWKClient

_jwks_client_cache = {"client": None, "loaded_at": 0, "url": None}
_static_jwks_cache = {"keys": None, "loaded_at": 0, "path": None}


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _jwks_url(config) -> str:
    if config.KEYCLOAK_JWKS_URL:
        return config.KEYCLOAK_JWKS_URL
    return f"{config.KEYCLOAK_ISSUER}/protocol/openid-connect/certs"


def _get_signing_key(token: str, config):
    """
    Resolve the RSA public key that signed `token`, either from a local
    static JWKS file (offline/dev) or Keycloak's live JWKS endpoint
    (cached in-process for KEYCLOAK_JWKS_CACHE_TTL_SECONDS).
    """
    if config.KEYCLOAK_JWKS_STATIC_FILE:
        now = time.time()
        path_changed = _static_jwks_cache["path"] != config.KEYCLOAK_JWKS_STATIC_FILE
        stale = now - _static_jwks_cache["loaded_at"] > config.KEYCLOAK_JWKS_CACHE_TTL_SECONDS
        if _static_jwks_cache["keys"] is None or path_changed or stale:
            if not os.path.exists(config.KEYCLOAK_JWKS_STATIC_FILE):
                raise AuthError(f"KEYCLOAK_JWKS_STATIC_FILE not found: {config.KEYCLOAK_JWKS_STATIC_FILE}", 500)
            with open(config.KEYCLOAK_JWKS_STATIC_FILE) as f:
                jwks_doc = json.load(f)
            _static_jwks_cache["keys"] = {k["kid"]: jwt.PyJWK(k) for k in jwks_doc["keys"]}
            _static_jwks_cache["loaded_at"] = now
            _static_jwks_cache["path"] = config.KEYCLOAK_JWKS_STATIC_FILE
        header = jwt.get_unverified_header(token)
        key = _static_jwks_cache["keys"].get(header.get("kid"))
        if key is None:
            raise AuthError("No matching key in static JWKS for token 'kid'", 401)
        return key.key

    now = time.time()
    url_changed = _jwks_client_cache["url"] != _jwks_url(config)
    stale = now - _jwks_client_cache["loaded_at"] > config.KEYCLOAK_JWKS_CACHE_TTL_SECONDS
    if _jwks_client_cache["client"] is None or url_changed or stale:
        _jwks_client_cache["client"] = PyJWKClient(_jwks_url(config), cache_keys=True)
        _jwks_client_cache["loaded_at"] = now
        _jwks_client_cache["url"] = _jwks_url(config)
    try:
        signing_key = _jwks_client_cache["client"].get_signing_key_from_jwt(token)
    except Exception as exc:  # noqa: BLE001 - surfaced as a 401 below
        raise AuthError(f"Unable to resolve signing key: {exc}", 401)
    return signing_key.key


def decode_and_validate(token: str, config) -> dict:
    signing_key = _get_signing_key(token, config)
    try:
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=config.KEYCLOAK_ALGORITHMS,
            issuer=config.KEYCLOAK_ISSUER,
            audience=config.KEYCLOAK_AUDIENCE,
            leeway=config.KEYCLOAK_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired", 401)
    except jwt.InvalidAudienceError:
        raise AuthError("Token audience does not include this Gateway", 401)
    except jwt.InvalidIssuerError:
        raise AuthError("Token issuer does not match configured Keycloak realm", 401)
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid token: {exc}", 401)
    return claims


def _extract_bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header (expected 'Bearer <token>')", 401)
    return auth_header[len("Bearer "):].strip()


def require_department_auth(f):
    """
    Decorator for endpoints that take a JSON body containing "dept_code".
    Populates flask.g.auth with the validated claims and enforces that
    the token's department claim matches the requested dept_code.

    In DEV_BYPASS_AUTH mode (dev environment only), trusts
    X-Debug-Department / X-Debug-User / X-Debug-Roles headers instead of
    a real token, so the Gateway can be exercised without a live
    Keycloak instance during local development.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        config = current_app.config["APP_CONFIG"]
        body = request.get_json(silent=True) or {}
        dept_code = (body.get("dept_code") or "").strip().upper()
        if not dept_code:
            return jsonify(error="dept_code is required in the request body"), 400

        if config.DEV_BYPASS_AUTH:
            if config.ENV != "development":
                # Refuse to run this way outside dev, even if misconfigured.
                return jsonify(error="DEV_BYPASS_AUTH is only permitted when FLASK_ENV=development"), 500
            debug_dept = request.headers.get("X-Debug-Department", dept_code)
            g.auth = {
                "sub": request.headers.get("X-Debug-User", "dev-user"),
                "department": debug_dept,
                "realm_access": {"roles": (request.headers.get("X-Debug-Roles", "")).split(",")},
                "preferred_username": request.headers.get("X-Debug-User", "dev-user"),
            }
        else:
            try:
                token = _extract_bearer_token()
                claims = decode_and_validate(token, config)
            except AuthError as exc:
                return jsonify(error=exc.message), exc.status_code

            token_dept = str(claims.get(config.KEYCLOAK_DEPARTMENT_CLAIM, "")).strip().upper()
            if not token_dept:
                return jsonify(error=f"Token missing '{config.KEYCLOAK_DEPARTMENT_CLAIM}' claim"), 403
            if token_dept != dept_code:
                return jsonify(
                    error=f"Token department '{token_dept}' does not authorize access to '{dept_code}'"
                ), 403
            g.auth = claims

        return f(*args, **kwargs)

    return wrapper
