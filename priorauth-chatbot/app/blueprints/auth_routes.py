"""
PKCE authorization-code flow against the Central Realm. This chatbot is
registered in Keycloak as a *public* client (no client_secret) — PKCE is
what makes that safe.

Flow:
  GET  /auth/login    -> redirect to Keycloak's /auth endpoint with a
                          freshly generated code_challenge and a signed
                          `state` carrying the code_verifier
  GET  /auth/callback -> exchange the returned `code` (+ code_verifier
                          recovered from `state`) for tokens, verify the
                          token, populate the Flask session
  GET  /auth/logout   -> clear the session and redirect to Keycloak's
                          end-session endpoint
"""
import time

import requests
from flask import Blueprint, current_app, redirect, request, session, url_for

from ..security.jwt_verify import TokenVerificationError, verify_token
from ..security.pkce import StateError, derive_code_challenge, generate_code_verifier, pack_state, unpack_state

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.get("/login")
def login():
    config = current_app.config["APP_CONFIG"]
    next_url = request.args.get("next") or url_for("chat.index")

    if config.DEV_BYPASS_AUTH:
        return redirect(url_for("auth.dev_login", next=next_url))

    code_verifier = generate_code_verifier()
    code_challenge = derive_code_challenge(code_verifier)
    state = pack_state(config.SECRET_KEY, code_verifier=code_verifier, next_url=next_url)

    params = {
        "client_id": config.KEYCLOAK_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": config.KEYCLOAK_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = requests.Request("GET", config.derived_authorization_endpoint(), params=params).prepare().url
    return redirect(auth_url)


@auth_bp.get("/callback")
def callback():
    config = current_app.config["APP_CONFIG"]
    logger = current_app.extensions["chat_logger"]

    error = request.args.get("error")
    if error:
        return f"Login failed: {error} — {request.args.get('error_description', '')}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return "Missing 'code' or 'state' from Keycloak callback", 400

    try:
        state_data = unpack_state(config.SECRET_KEY, state, config.OAUTH_STATE_MAX_AGE_SECONDS)
    except StateError as exc:
        return str(exc), 400

    token_resp = requests.post(
        config.derived_token_endpoint(),
        data={
            "grant_type": "authorization_code",
            "client_id": config.KEYCLOAK_CLIENT_ID,
            "code": code,
            "redirect_uri": config.OAUTH_REDIRECT_URI,
            "code_verifier": state_data["cv"],
        },
        timeout=15,
    )
    if token_resp.status_code != 200:
        logger.error(f'{{"log_type":"auth_error","stage":"token_exchange","status":{token_resp.status_code}}}')
        return f"Token exchange failed ({token_resp.status_code}): {token_resp.text}", 502

    tokens = token_resp.json()
    access_token = tokens["access_token"]

    try:
        claims = verify_token(access_token, config)
    except TokenVerificationError as exc:
        logger.error(f'{{"log_type":"auth_error","stage":"token_verify","error":"{exc}"}}')
        return f"Token verification failed: {exc}", 401

    session["user"] = {
        "sub": claims.get("sub"),
        "username": claims.get("preferred_username", ""),
        "email": claims.get("email", ""),
        "department": claims.get(config.KEYCLOAK_DEPARTMENT_CLAIM, ""),
        "roles": claims.get("realm_access", {}).get("roles", []),
    }
    session["access_token"] = access_token
    session["id_token"] = tokens.get("id_token")
    session["access_token_expiry"] = time.time() + int(tokens.get("expires_in", 300))

    return redirect(state_data.get("next") or url_for("chat.index"))


@auth_bp.get("/dev-login")
def dev_login():
    """DEV ONLY — mints a local session without touching Keycloak."""
    config = current_app.config["APP_CONFIG"]
    if not (config.DEV_BYPASS_AUTH and config.ENV == "development"):
        return "DEV_BYPASS_AUTH is not enabled", 403

    session["user"] = {
        "sub": "dev-user-sub",
        "username": request.args.get("username", "dev.tester"),
        "email": "dev.tester@example.com",
        "department": config.DEPT_CODE,
        "roles": [f"{config.DEPT_CODE.lower()}-analyst"],
    }
    session["access_token"] = "dev-bypass-token"
    session["id_token"] = None
    session["access_token_expiry"] = time.time() + 3600
    return redirect(request.args.get("next") or url_for("chat.index"))


@auth_bp.get("/logout")
def logout():
    config = current_app.config["APP_CONFIG"]
    id_token = session.get("id_token")
    session.clear()

    if config.DEV_BYPASS_AUTH:
        return redirect(url_for("chat.index"))

    params = {"post_logout_redirect_uri": config.POST_LOGOUT_REDIRECT_URI}
    if id_token:
        params["id_token_hint"] = id_token
    logout_url = requests.Request("GET", config.derived_logout_endpoint(), params=params).prepare().url
    return redirect(logout_url)
