import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="providers"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="facprov-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_provider_when_provider_code_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("FACPROV")
        task_id = _insert_pending_task(dept_id, {
            "provider_code": "PRVTEST0001",
            "provider_name": "Test Medical Group",
            "npi_number": "1234567890",
            "specialty": "Family Medicine",
            "network_status": "PENDING_CREDENTIALING",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        provider = repository.get_provider_by_code(dept_id, "PRVTEST0001")
        assert provider is not None
        assert provider["network_status"] == "PENDING_CREDENTIALING"
        assert provider["npi_number"] == "1234567890"

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_provider_when_provider_code_matches(app):
    """Approving a HITL task whose provider_code already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO providers (dept_id, provider_code, provider_name, npi_number, specialty, "
                "network_status) VALUES (%s, 'PRVTEST0002', 'Test Medical Group', '1234567891', "
                "'Cardiology', 'PENDING_CREDENTIALING')",
                (repository.get_dept_id("FACPROV"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("FACPROV")
    task_id = _insert_pending_task(dept_id, {
        "provider_code": "PRVTEST0002",
        "provider_name": "Test Medical Group",
        "npi_number": "1234567891",
        "specialty": "Cardiology",
        "network_status": "IN_NETWORK",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    provider = repository.get_provider_by_code(dept_id, "PRVTEST0002")
    assert provider["network_status"] == "IN_NETWORK"  # updated, not duplicated

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM providers WHERE provider_code = 'PRVTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("FACPROV")
    task_id = _insert_pending_task(dept_id, {
        "provider_code": "PRVTEST0003",
        "provider_name": "Test Medical Group",
        "npi_number": "1234567892",
        "network_status": "PENDING_CREDENTIALING",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"network_status": "TERMINATED"})
    assert result["status"] == "EDITED"

    provider = repository.get_provider_by_code(dept_id, "PRVTEST0003")
    assert provider["network_status"] == "TERMINATED"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("FACPROV")
    task_id = _insert_pending_task(dept_id, {
        "provider_code": "PRVTEST0004",
        # provider_name / npi_number / network_status all missing
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("FACPROV")
    task_id = _insert_pending_task(dept_id, {
        "provider_code": "PRVTEST0005",
        "provider_name": "Test Medical Group",
        "npi_number": "1234567893",
        "network_status": "PENDING_CREDENTIALING",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_providers(app):
    dept_id = repository.get_dept_id("FACPROV")
    task_id = _insert_pending_task(dept_id, {
        "provider_code": "PRVTEST0006",
        "provider_name": "Test Medical Group",
        "npi_number": "1234567894",
        "network_status": "PENDING_CREDENTIALING",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_provider_by_code(dept_id, "PRVTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
