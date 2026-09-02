"""
Verifies the ID/access token Keycloak hands back after the PKCE code
exchange. Reused (in spirit — this module, not literally shared code)
by the central Gateway's own JWT validation of tokens relayed to it;
here we additionally check `azp` (authorized party) equals this
chatbot's own client id, which is how Keycloak marks which client a
token was minted for.
"""
import json
import os
import time

import jwt
from jwt import PyJWKClient

_jwks_client_cache = {"client": None, "loaded_at": 0, "url": None}
_static_jwks_cache = {"keys": None, "loaded_at": 0, "path": None}


class TokenVerificationError(Exception):
    pass


def _get_signing_key(token: str, config):
    if config.KEYCLOAK_JWKS_STATIC_FILE:
        now = time.time()
        path_changed = _static_jwks_cache["path"] != config.KEYCLOAK_JWKS_STATIC_FILE
        stale = now - _static_jwks_cache["loaded_at"] > 3600
        if _static_jwks_cache["keys"] is None or path_changed or stale:
            if not os.path.exists(config.KEYCLOAK_JWKS_STATIC_FILE):
                raise TokenVerificationError(f"JWKS static file not found: {config.KEYCLOAK_JWKS_STATIC_FILE}")
            with open(config.KEYCLOAK_JWKS_STATIC_FILE) as f:
                jwks_doc = json.load(f)
            _static_jwks_cache["keys"] = {k["kid"]: jwt.PyJWK(k) for k in jwks_doc["keys"]}
            _static_jwks_cache["loaded_at"] = now
            _static_jwks_cache["path"] = config.KEYCLOAK_JWKS_STATIC_FILE
        header = jwt.get_unverified_header(token)
        key = _static_jwks_cache["keys"].get(header.get("kid"))
        if key is None:
            raise TokenVerificationError("No matching key in static JWKS for token 'kid'")
        return key.key

    now = time.time()
    url = config.derived_jwks_url()
    url_changed = _jwks_client_cache["url"] != url
    stale = now - _jwks_client_cache["loaded_at"] > 3600
    if _jwks_client_cache["client"] is None or url_changed or stale:
        _jwks_client_cache["client"] = PyJWKClient(url, cache_keys=True)
        _jwks_client_cache["loaded_at"] = now
        _jwks_client_cache["url"] = url
    try:
        return _jwks_client_cache["client"].get_signing_key_from_jwt(token).key
    except Exception as exc:  # noqa: BLE001
        raise TokenVerificationError(f"Unable to resolve signing key: {exc}")


def verify_token(token: str, config) -> dict:
    signing_key = _get_signing_key(token, config)
    try:
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=config.KEYCLOAK_ALGORITHMS,
            issuer=config.KEYCLOAK_ISSUER,
            leeway=config.KEYCLOAK_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss"], "verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise TokenVerificationError("Token expired")
    except jwt.InvalidIssuerError:
        raise TokenVerificationError("Token issuer does not match configured Keycloak realm")
    except jwt.PyJWTError as exc:
        raise TokenVerificationError(f"Invalid token: {exc}")

    azp = claims.get("azp")
    if azp and azp != config.KEYCLOAK_CLIENT_ID:
        raise TokenVerificationError(f"Token was not issued for this client (azp={azp})")
    return claims
