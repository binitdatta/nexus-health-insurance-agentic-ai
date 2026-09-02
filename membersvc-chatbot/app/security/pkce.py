"""
PKCE (RFC 7636) helpers, plus a *stateless* signed state parameter.

Why stateless state: storing the PKCE code_verifier in a server-side
session keyed by a cookie set on the /auth/login redirect can break on
some browsers' SameSite handling of the round trip through Keycloak.
Instead we embed the code_verifier (and the post-login destination)
directly inside the `state` parameter itself, signed with itsdangerous
so it can't be tampered with, and time-limited so an intercepted state
value is useless after OAUTH_STATE_MAX_AGE_SECONDS.
"""
import base64
import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_STATE_SALT = "membersvc-chatbot-oauth-state"


def generate_code_verifier() -> str:
    # 96 random bytes -> ~128 base64url chars, within the 43-128 range RFC 7636 requires.
    return base64.urlsafe_b64encode(secrets.token_bytes(96)).rstrip(b"=").decode("ascii")


def derive_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def pack_state(secret_key: str, *, code_verifier: str, next_url: str) -> str:
    serializer = URLSafeTimedSerializer(secret_key, salt=_STATE_SALT)
    nonce = secrets.token_urlsafe(16)
    return serializer.dumps({"cv": code_verifier, "next": next_url, "n": nonce})


class StateError(Exception):
    pass


def unpack_state(secret_key: str, state: str, max_age_seconds: int) -> dict:
    serializer = URLSafeTimedSerializer(secret_key, salt=_STATE_SALT)
    try:
        return serializer.loads(state, max_age=max_age_seconds)
    except SignatureExpired:
        raise StateError("OAuth state expired — please try logging in again")
    except BadSignature:
        raise StateError("OAuth state signature invalid")
