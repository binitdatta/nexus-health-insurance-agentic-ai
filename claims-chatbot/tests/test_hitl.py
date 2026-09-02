import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="claims"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="claims-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_claim_when_claim_number_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("CLAIMS")
        task_id = _insert_pending_task(dept_id, {
            "claim_number": "CLMTEST0001",
            "member_id": "MBRTEST01",
            "provider_id": "PRVTEST01",
            "date_of_service": "2026-05-01",
            "claim_status": "SUBMITTED",
            "notes": "TEST_ROW_SAFE_TO_DELETE",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        claim = repository.get_claim_by_number(dept_id, "CLMTEST0001")
        assert claim is not None
        assert claim["claim_status"] == "SUBMITTED"
        assert claim["member_id"] == "MBRTEST01"

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_claim_when_claim_number_matches(app):
    """Approving a HITL task whose claim_number already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO claims (dept_id, claim_number, member_id, provider_id, date_of_service, "
                "claim_status, notes) VALUES (%s, 'CLMTEST0002', 'MBRTEST02', 'PRVTEST02', '2026-04-01', "
                "'SUBMITTED', 'TEST_ROW_SAFE_TO_DELETE')",
                (repository.get_dept_id("CLAIMS"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("CLAIMS")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0002",
        "member_id": "MBRTEST02",
        "provider_id": "PRVTEST02",
        "date_of_service": "2026-04-01",
        "claim_status": "APPEALED",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    claim = repository.get_claim_by_number(dept_id, "CLMTEST0002")
    assert claim["claim_status"] == "APPEALED"  # updated, not duplicated

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM claims WHERE claim_number = 'CLMTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("CLAIMS")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0003",
        "member_id": "MBRTEST03",
        "provider_id": "PRVTEST03",
        "date_of_service": "2026-03-01",
        "claim_status": "SUBMITTED",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"claim_status": "APPROVED"})
    assert result["status"] == "EDITED"

    claim = repository.get_claim_by_number(dept_id, "CLMTEST0003")
    assert claim["claim_status"] == "APPROVED"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("CLAIMS")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0004",
        # member_id / provider_id / date_of_service / claim_status all missing
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("CLAIMS")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0005",
        "member_id": "MBRTEST05",
        "provider_id": "PRVTEST05",
        "date_of_service": "2026-02-01",
        "claim_status": "SUBMITTED",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_claims(app):
    dept_id = repository.get_dept_id("CLAIMS")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0006",
        "member_id": "MBRTEST06",
        "provider_id": "PRVTEST06",
        "date_of_service": "2026-01-15",
        "claim_status": "SUBMITTED",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_claim_by_number(dept_id, "CLMTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
