import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="call_center_logs"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="callcenter-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_call_when_call_reference_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("CALLCENTER")
        task_id = _insert_pending_task(dept_id, {
            "call_reference": "CALLTEST0001",
            "member_id": "MBRTEST01",
            "agent_id": "AGTTEST01",
            "call_datetime": "2026-05-01 10:00:00",
            "call_type": "BENEFITS",
            "resolution_status": "RESOLVED",
            "csat_score": 5,
            "call_notes": "TEST_ROW_SAFE_TO_DELETE",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        call = repository.get_call_by_reference(dept_id, "CALLTEST0001")
        assert call is not None
        assert call["resolution_status"] == "RESOLVED"
        assert call["csat_score"] == 5

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_call_when_call_reference_matches(app):
    """Approving a HITL task whose call_reference already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO call_center_logs (dept_id, call_reference, member_id, agent_id, call_datetime, "
                "call_type, resolution_status, call_notes) VALUES (%s, 'CALLTEST0002', 'MBRTEST02', "
                "'AGTTEST02', '2026-04-01 09:00:00', 'COMPLAINT', 'ESCALATED', 'TEST_ROW_SAFE_TO_DELETE')",
                (repository.get_dept_id("CALLCENTER"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("CALLCENTER")
    task_id = _insert_pending_task(dept_id, {
        "call_reference": "CALLTEST0002",
        "member_id": "MBRTEST02",
        "agent_id": "AGTTEST02",
        "call_datetime": "2026-04-01 09:00:00",
        "call_type": "COMPLAINT",
        "resolution_status": "RESOLVED",
        "csat_score": 3,
        "call_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    call = repository.get_call_by_reference(dept_id, "CALLTEST0002")
    assert call["resolution_status"] == "RESOLVED"  # updated, not duplicated
    assert call["csat_score"] == 3

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM call_center_logs WHERE call_reference = 'CALLTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("CALLCENTER")
    task_id = _insert_pending_task(dept_id, {
        "call_reference": "CALLTEST0003",
        "member_id": "MBRTEST03",
        "agent_id": "AGTTEST03",
        "call_datetime": "2026-03-01 14:00:00",
        "call_type": "ENROLLMENT",
        "resolution_status": "FOLLOW_UP_NEEDED",
        "call_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"resolution_status": "RESOLVED", "csat_score": 4})
    assert result["status"] == "EDITED"

    call = repository.get_call_by_reference(dept_id, "CALLTEST0003")
    assert call["resolution_status"] == "RESOLVED"
    assert call["csat_score"] == 4


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("CALLCENTER")
    task_id = _insert_pending_task(dept_id, {
        "call_reference": "CALLTEST0004",
        # member_id / agent_id / call_datetime / call_type / resolution_status all missing
        "call_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("CALLCENTER")
    task_id = _insert_pending_task(dept_id, {
        "call_reference": "CALLTEST0005",
        "member_id": "MBRTEST05",
        "agent_id": "AGTTEST05",
        "call_datetime": "2026-02-01 11:00:00",
        "call_type": "CLAIMS_STATUS",
        "resolution_status": "RESOLVED",
        "call_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_call_center_logs(app):
    dept_id = repository.get_dept_id("CALLCENTER")
    task_id = _insert_pending_task(dept_id, {
        "call_reference": "CALLTEST0006",
        "member_id": "MBRTEST06",
        "agent_id": "AGTTEST06",
        "call_datetime": "2026-01-15 15:00:00",
        "call_type": "PROVIDER_SEARCH",
        "resolution_status": "RESOLVED",
        "call_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_call_by_reference(dept_id, "CALLTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
