import json

from .conftest import TEST_AUDIENCE, TEST_ISSUER, make_token


def test_health(app_with_dev_bypass):
    client = app_with_dev_bypass.test_client()
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json["status"] == "ok"


def test_intent_dev_bypass(app_with_dev_bypass):
    client = app_with_dev_bypass.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={
            "dept_code": "CLAIMS",
            "chatbot_source": "claims-chatbot",
            "session_id": "sess-1",
            "message": "What is the status of claim CLM100005?",
        },
        headers={"X-Debug-User": "tester", "X-Debug-Department": "CLAIMS"},
    )
    assert resp.status_code == 200, resp.data
    body = resp.json
    assert body["intent"] == "data_lookup"
    assert body["entities"]["claim_number"] == "CLM100005"
    assert body["usage"]["total_cost_usd"] > 0


def test_respond_dev_bypass(app_with_dev_bypass):
    client = app_with_dev_bypass.test_client()
    resp = client.post(
        "/api/v1/llm/respond",
        json={
            "dept_code": "CLAIMS",
            "chatbot_source": "claims-chatbot",
            "session_id": "sess-1",
            "message": "What is the status of claim CLM100005?",
            "intent": "data_lookup",
            "retrieved_context": [
                {"source": "claims_row_CLM100005", "type": "sql_row", "content": "claim_number=CLM100005, status=IN_REVIEW"}
            ],
        },
    )
    assert resp.status_code == 200, resp.data
    body = resp.json
    assert "IN_REVIEW" in body["answer_markdown"]
    assert body["render"]["type"] == "table"


def test_hitl_draft_dev_bypass(app_with_dev_bypass):
    client = app_with_dev_bypass.test_client()
    resp = client.post(
        "/api/v1/llm/hitl-draft",
        json={
            "dept_code": "CLAIMS",
            "chatbot_source": "claims-chatbot",
            "session_id": "sess-1",
            "message": "Please file an appeal for claim CLM100005",
            "entity_type": "claims",
            "allowed_fields": ["claim_number", "member_id", "provider_id", "date_of_service", "claim_status"],
            "required_fields": ["claim_number", "member_id", "provider_id", "date_of_service", "claim_status"],
        },
    )
    assert resp.status_code == 200, resp.data
    body = resp.json
    assert body["proposed_payload"]["claim_status"] == "APPEALED"
    assert "notes" in body["missing_fields"]


def test_missing_required_field_returns_400(app_with_dev_bypass):
    client = app_with_dev_bypass.test_client()
    resp = client.post("/api/v1/llm/intent", json={"dept_code": "CLAIMS"})
    assert resp.status_code == 400
    assert "Missing required field" in resp.json["error"]


# --- Real JWT / Keycloak-shaped auth path -----------------------------

def test_missing_token_rejected(app_with_real_auth):
    client = app_with_real_auth.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={"dept_code": "CLAIMS", "chatbot_source": "claims-chatbot", "session_id": "s1", "message": "hi"},
    )
    assert resp.status_code == 401
    assert "Authorization header" in resp.json["error"]


def test_valid_token_matching_department_succeeds(app_with_real_auth, rsa_keypair):
    private_key, _ = rsa_keypair
    token = make_token(private_key, department="CLAIMS")
    client = app_with_real_auth.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={"dept_code": "CLAIMS", "chatbot_source": "claims-chatbot", "session_id": "s1", "message": "status of CLM100005?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.data


def test_token_department_mismatch_rejected(app_with_real_auth, rsa_keypair):
    """The core RBAC guarantee: a Claims department token must not be
    usable to call the Gateway on behalf of Billing."""
    private_key, _ = rsa_keypair
    token = make_token(private_key, department="CLAIMS")
    client = app_with_real_auth.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={"dept_code": "BILLING", "chatbot_source": "billing-chatbot", "session_id": "s1", "message": "show unpaid invoices"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "does not authorize" in resp.json["error"]


def test_expired_token_rejected(app_with_real_auth, rsa_keypair):
    private_key, _ = rsa_keypair
    token = make_token(private_key, department="CLAIMS", expired=True)
    client = app_with_real_auth.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={"dept_code": "CLAIMS", "chatbot_source": "claims-chatbot", "session_id": "s1", "message": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json["error"].lower()


def test_wrong_audience_rejected(app_with_real_auth, rsa_keypair):
    private_key, _ = rsa_keypair
    token = make_token(private_key, department="CLAIMS", aud="some-other-api")
    client = app_with_real_auth.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={"dept_code": "CLAIMS", "chatbot_source": "claims-chatbot", "session_id": "s1", "message": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "audience" in resp.json["error"].lower()


def test_wrong_issuer_rejected(app_with_real_auth, rsa_keypair):
    private_key, _ = rsa_keypair
    token = make_token(private_key, department="CLAIMS", iss="https://not-our-realm.example.com")
    client = app_with_real_auth.test_client()
    resp = client.post(
        "/api/v1/llm/intent",
        json={"dept_code": "CLAIMS", "chatbot_source": "claims-chatbot", "session_id": "s1", "message": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "issuer" in resp.json["error"].lower()