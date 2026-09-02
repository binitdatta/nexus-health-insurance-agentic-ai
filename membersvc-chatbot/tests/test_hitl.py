import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="member_services_tickets"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="membersvc-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_ticket_when_ticket_number_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("MEMBERSVC")
        task_id = _insert_pending_task(dept_id, {
            "ticket_number": "TIXTEST0001",
            "member_id": "MBRTEST01",
            "agent_id": "AGTTEST01",
            "category": "GRIEVANCE",
            "priority": "HIGH",
            "status": "OPEN",
            "opened_date": "2026-05-01",
            "resolution_notes": "TEST_ROW_SAFE_TO_DELETE",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        ticket = repository.get_ticket_by_number(dept_id, "TIXTEST0001")
        assert ticket is not None
        assert ticket["status"] == "OPEN"
        assert ticket["category"] == "GRIEVANCE"

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_ticket_when_ticket_number_matches(app):
    """Approving a HITL task whose ticket_number already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO member_services_tickets (dept_id, ticket_number, member_id, agent_id, category, "
                "priority, status, opened_date, resolution_notes) VALUES (%s, 'TIXTEST0002', 'MBRTEST02', "
                "'AGTTEST02', 'ID_CARD', 'LOW', 'OPEN', '2026-04-01', 'TEST_ROW_SAFE_TO_DELETE')",
                (repository.get_dept_id("MEMBERSVC"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("MEMBERSVC")
    task_id = _insert_pending_task(dept_id, {
        "ticket_number": "TIXTEST0002",
        "member_id": "MBRTEST02",
        "agent_id": "AGTTEST02",
        "category": "ID_CARD",
        "priority": "LOW",
        "status": "RESOLVED",
        "opened_date": "2026-04-01",
        "closed_date": "2026-04-05",
        "resolution_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    ticket = repository.get_ticket_by_number(dept_id, "TIXTEST0002")
    assert ticket["status"] == "RESOLVED"  # updated, not duplicated

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM member_services_tickets WHERE ticket_number = 'TIXTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("MEMBERSVC")
    task_id = _insert_pending_task(dept_id, {
        "ticket_number": "TIXTEST0003",
        "member_id": "MBRTEST03",
        "agent_id": "AGTTEST03",
        "category": "COVERAGE_QUESTION",
        "priority": "MEDIUM",
        "status": "OPEN",
        "opened_date": "2026-03-01",
        "resolution_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"status": "CLOSED", "priority": "LOW"})
    assert result["status"] == "EDITED"

    ticket = repository.get_ticket_by_number(dept_id, "TIXTEST0003")
    assert ticket["status"] == "CLOSED"
    assert ticket["priority"] == "LOW"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("MEMBERSVC")
    task_id = _insert_pending_task(dept_id, {
        "ticket_number": "TIXTEST0004",
        # member_id / agent_id / category / priority / status / opened_date all missing
        "resolution_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("MEMBERSVC")
    task_id = _insert_pending_task(dept_id, {
        "ticket_number": "TIXTEST0005",
        "member_id": "MBRTEST05",
        "agent_id": "AGTTEST05",
        "category": "ENROLLMENT",
        "priority": "MEDIUM",
        "status": "OPEN",
        "opened_date": "2026-02-01",
        "resolution_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_member_services_tickets(app):
    dept_id = repository.get_dept_id("MEMBERSVC")
    task_id = _insert_pending_task(dept_id, {
        "ticket_number": "TIXTEST0006",
        "member_id": "MBRTEST06",
        "agent_id": "AGTTEST06",
        "category": "ADDRESS_CHANGE",
        "priority": "LOW",
        "status": "OPEN",
        "opened_date": "2026-01-15",
        "resolution_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_ticket_by_number(dept_id, "TIXTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
