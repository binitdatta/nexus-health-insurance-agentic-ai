import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="prior_authorizations"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="priorauth-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_pa_when_pa_number_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("PRIORAUTH")
        task_id = _insert_pending_task(dept_id, {
            "pa_number": "PATEST0001",
            "member_id": "MBRTEST01",
            "provider_id": "PRVTEST01",
            "procedure_code": "99213",
            "requested_date": "2026-05-01",
            "urgency": "ROUTINE",
            "status": "PENDING",
            "clinical_notes": "TEST_ROW_SAFE_TO_DELETE",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        pa = repository.get_pa_by_number(dept_id, "PATEST0001")
        assert pa is not None
        assert pa["status"] == "PENDING"
        assert pa["urgency"] == "ROUTINE"

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_pa_when_pa_number_matches(app):
    """Approving a HITL task whose pa_number already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prior_authorizations (dept_id, pa_number, member_id, provider_id, procedure_code, "
                "requested_date, urgency, status, clinical_notes) VALUES (%s, 'PATEST0002', 'MBRTEST02', "
                "'PRVTEST02', '99214', '2026-04-01', 'ROUTINE', 'PENDING', 'TEST_ROW_SAFE_TO_DELETE')",
                (repository.get_dept_id("PRIORAUTH"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("PRIORAUTH")
    task_id = _insert_pending_task(dept_id, {
        "pa_number": "PATEST0002",
        "member_id": "MBRTEST02",
        "provider_id": "PRVTEST02",
        "procedure_code": "99214",
        "requested_date": "2026-04-01",
        "urgency": "URGENT",
        "status": "APPROVED",
        "decision_reason": "Clinical criteria met",
        "clinical_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    pa = repository.get_pa_by_number(dept_id, "PATEST0002")
    assert pa["status"] == "APPROVED"  # updated, not duplicated
    assert pa["urgency"] == "URGENT"

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM prior_authorizations WHERE pa_number = 'PATEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("PRIORAUTH")
    task_id = _insert_pending_task(dept_id, {
        "pa_number": "PATEST0003",
        "member_id": "MBRTEST03",
        "provider_id": "PRVTEST03",
        "procedure_code": "99213",
        "requested_date": "2026-03-01",
        "urgency": "ROUTINE",
        "status": "PENDING",
        "clinical_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"status": "DENIED", "decision_reason": "Insufficient clinical documentation"})
    assert result["status"] == "EDITED"

    pa = repository.get_pa_by_number(dept_id, "PATEST0003")
    assert pa["status"] == "DENIED"
    assert pa["decision_reason"] == "Insufficient clinical documentation"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("PRIORAUTH")
    task_id = _insert_pending_task(dept_id, {
        "pa_number": "PATEST0004",
        # member_id / provider_id / procedure_code / requested_date / urgency / status all missing
        "clinical_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("PRIORAUTH")
    task_id = _insert_pending_task(dept_id, {
        "pa_number": "PATEST0005",
        "member_id": "MBRTEST05",
        "provider_id": "PRVTEST05",
        "procedure_code": "99213",
        "requested_date": "2026-02-01",
        "urgency": "ROUTINE",
        "status": "PENDING",
        "clinical_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_prior_authorizations(app):
    dept_id = repository.get_dept_id("PRIORAUTH")
    task_id = _insert_pending_task(dept_id, {
        "pa_number": "PATEST0006",
        "member_id": "MBRTEST06",
        "provider_id": "PRVTEST06",
        "procedure_code": "99213",
        "requested_date": "2026-01-15",
        "urgency": "ROUTINE",
        "status": "PENDING",
        "clinical_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_pa_by_number(dept_id, "PATEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
