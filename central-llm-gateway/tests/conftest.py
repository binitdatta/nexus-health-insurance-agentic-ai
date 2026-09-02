import json
import os
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app
from app.config import Config

TEST_ISSUER = "https://test-issuer.example.com/realms/health-ai-central"
TEST_AUDIENCE = "central-llm-api"


class _FakeResponse:
    def __init__(self, tool_name: str, tool_input: dict, input_tokens: int = 120, output_tokens: int = 45):
        self.id = f"msg_{uuid.uuid4().hex[:12]}"
        self.stop_reason = "tool_use"

        class _Block:
            type = "tool_use"

        block = _Block()
        block.id = f"toolu_{uuid.uuid4().hex[:12]}"
        block.name = tool_name
        block.input = tool_input
        self.content = [block]

        class _Usage:
            pass

        self.usage = _Usage()
        self.usage.input_tokens = input_tokens
        self.usage.output_tokens = output_tokens


class FakeMessages:
    """Stands in for anthropic.Anthropic().messages — returns a
    canned tool_use response shaped to whichever tool was forced,
    so the Gateway's parsing/logging logic gets exercised without
    a real network call."""

    def create(self, *, model, max_tokens, system, messages, tools, tool_choice):
        tool_name = tool_choice["name"]
        if tool_name == "record_intent":
            payload = {
                "intent": "data_lookup",
                "confidence": 0.92,
                "entities": {"claim_number": "CLM100005"},
                "suggested_render": "table",
            }
        elif tool_name == "final_response":
            payload = {
                "answer_markdown": "Claim **CLM100005** is currently **IN_REVIEW**.",
                "render": {"type": "table", "spec": {"columns": ["claim_number", "status"], "rows": [["CLM100005", "IN_REVIEW"]]}},
                "citations": ["claims_row_CLM100005"],
                "confidence": 0.88,
            }
        elif tool_name == "propose_record":
            payload = {
                "proposed_payload": {"member_id": "MBR12345", "claim_status": "APPEALED"},
                "missing_fields": ["notes"],
                "rationale": "User asked to file an appeal for this claim.",
            }
        else:
            raise AssertionError(f"Unexpected tool_choice in test: {tool_name}")
        return _FakeResponse(tool_name, payload)


class FakeAnthropicSDK:
    def __init__(self):
        self.messages = FakeMessages()


@pytest.fixture()
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture()
def jwks_file(tmp_path, rsa_keypair):
    _, public_key = rsa_keypair
    jwk_json = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    jwk_json["kid"] = "test-key-1"
    jwk_json["use"] = "sig"
    jwk_json["alg"] = "RS256"
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk_json]}))
    return str(path)


def make_token(private_key, *, department: str, sub: str = "dev-user-sub", roles=None, aud: str = TEST_AUDIENCE, iss: str = TEST_ISSUER, expired: bool = False):
    now = int(time.time())
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "iat": now,
        "exp": now - 10 if expired else now + 3600,
        "preferred_username": f"{sub}@example.com",
        "email": f"{sub}@example.com",
        "department": department,
        "realm_access": {"roles": roles or ["claims-analyst"]},
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key-1"})


@pytest.fixture()
def app_with_real_auth(jwks_file, monkeypatch):
    class TestConfig(Config):
        DEV_BYPASS_AUTH = False
        ENV = "development"
        KEYCLOAK_ISSUER = TEST_ISSUER
        KEYCLOAK_AUDIENCE = TEST_AUDIENCE
        KEYCLOAK_JWKS_STATIC_FILE = jwks_file

    flask_app = create_app(TestConfig)
    flask_app.extensions["anthropic_client"] = _wrap_fake(flask_app)
    return flask_app


@pytest.fixture()
def app_with_dev_bypass(monkeypatch):
    class TestConfig(Config):
        DEV_BYPASS_AUTH = True
        ENV = "development"

    flask_app = create_app(TestConfig)
    flask_app.extensions["anthropic_client"] = _wrap_fake(flask_app)
    return flask_app


def _wrap_fake(flask_app):
    from app.anthropic_client import AnthropicGatewayClient

    return AnthropicGatewayClient(flask_app.config["APP_CONFIG"], sdk_client=FakeAnthropicSDK())
