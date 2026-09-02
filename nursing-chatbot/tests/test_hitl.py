import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="nursing_cases"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="nursing-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_case_when_case_number_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("NURSING")
        task_id = _insert_pending_task(dept_id, {
            "case_number": "NCTEST0001",
            "member_id": "MBRTEST01",
            "nurse_id": "RN999",
            "case_type": "CARE_MANAGEMENT",
            "acuity_level": "MEDIUM",
            "status": "OPEN",
            "opened_date": "2026-05-01",
            "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        case = repository.get_case_by_number(dept_id, "NCTEST0001")
        assert case is not None
        assert case["status"] == "OPEN"
        assert case["acuity_level"] == "MEDIUM"

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_case_when_case_number_matches(app):
    """Approving a HITL task whose case_number already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO nursing_cases (dept_id, case_number, member_id, nurse_id, case_type, "
                "acuity_level, status, opened_date, care_plan_notes) VALUES (%s, 'NCTEST0002', 'MBRTEST02', "
                "'RN999', 'CARE_MANAGEMENT', 'LOW', 'OPEN', '2026-04-01', 'TEST_ROW_SAFE_TO_DELETE')",
                (repository.get_dept_id("NURSING"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("NURSING")
    task_id = _insert_pending_task(dept_id, {
        "case_number": "NCTEST0002",
        "member_id": "MBRTEST02",
        "nurse_id": "RN999",
        "case_type": "CARE_MANAGEMENT",
        "acuity_level": "CRITICAL",
        "status": "IN_PROGRESS",
        "opened_date": "2026-04-01",
        "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    case = repository.get_case_by_number(dept_id, "NCTEST0002")
    assert case["status"] == "IN_PROGRESS"  # updated, not duplicated
    assert case["acuity_level"] == "CRITICAL"

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM nursing_cases WHERE case_number = 'NCTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("NURSING")
    task_id = _insert_pending_task(dept_id, {
        "case_number": "NCTEST0003",
        "member_id": "MBRTEST03",
        "nurse_id": "RN999",
        "case_type": "DISCHARGE_PLANNING",
        "acuity_level": "HIGH",
        "status": "OPEN",
        "opened_date": "2026-03-01",
        "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"status": "CLOSED", "closed_date": "2026-03-15"})
    assert result["status"] == "EDITED"

    case = repository.get_case_by_number(dept_id, "NCTEST0003")
    assert case["status"] == "CLOSED"
    assert str(case["closed_date"]) == "2026-03-15"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("NURSING")
    task_id = _insert_pending_task(dept_id, {
        "case_number": "NCTEST0004",
        # member_id / nurse_id / case_type / acuity_level / status / opened_date all missing
        "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("NURSING")
    task_id = _insert_pending_task(dept_id, {
        "case_number": "NCTEST0005",
        "member_id": "MBRTEST05",
        "nurse_id": "RN999",
        "case_type": "UTILIZATION_REVIEW",
        "acuity_level": "LOW",
        "status": "OPEN",
        "opened_date": "2026-02-01",
        "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_nursing_cases(app):
    dept_id = repository.get_dept_id("NURSING")
    task_id = _insert_pending_task(dept_id, {
        "case_number": "NCTEST0006",
        "member_id": "MBRTEST06",
        "nurse_id": "RN999",
        "case_type": "DISEASE_MANAGEMENT",
        "acuity_level": "MEDIUM",
        "status": "OPEN",
        "opened_date": "2026-01-15",
        "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_case_by_number(dept_id, "NCTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
