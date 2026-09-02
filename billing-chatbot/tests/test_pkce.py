import base64
import hashlib
import time

import pytest

from app.security.pkce import (
    StateError,
    derive_code_challenge,
    generate_code_verifier,
    pack_state,
    unpack_state,
)


def test_code_verifier_length_within_rfc7636_bounds():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert set(verifier) <= allowed


def test_code_challenge_matches_s256_spec():
    verifier = "test-verifier-abcDEF123-_~" * 3
    challenge = derive_code_challenge(verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_state_roundtrip():
    state = pack_state("secret", code_verifier="verifier123", next_url="/dashboard")
    data = unpack_state("secret", state, max_age_seconds=60)
    assert data["cv"] == "verifier123"
    assert data["next"] == "/dashboard"


def test_state_tamper_detected():
    state = pack_state("secret", code_verifier="verifier123", next_url="/dashboard")
    tampered = state[:-1] + ("A" if state[-1] != "A" else "B")
    with pytest.raises(StateError):
        unpack_state("secret", tampered, max_age_seconds=60)


def test_state_wrong_secret_rejected():
    state = pack_state("secret-a", code_verifier="verifier123", next_url="/dashboard")
    with pytest.raises(StateError):
        unpack_state("secret-b", state, max_age_seconds=60)


def test_state_expiry_enforced():
    state = pack_state("secret", code_verifier="verifier123", next_url="/dashboard")
    time.sleep(2.2)
    with pytest.raises(StateError):
        unpack_state("secret", state, max_age_seconds=1)
