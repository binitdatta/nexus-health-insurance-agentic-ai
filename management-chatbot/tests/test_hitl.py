import json

import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="management_reports"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="management-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_report_when_report_ref_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("MANAGEMENT")
        task_id = _insert_pending_task(dept_id, {
            "report_ref": "RPTTEST0001",
            "report_title": "Test Report",
            "covers_dept_id": 1,
            "report_period": "2026-06",
            "report_date": "2026-06-01",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        report = repository.get_report_by_ref(dept_id, "RPTTEST0001")
        assert report is not None
        assert report["covers_dept_id"] == 1

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_resolves_covers_dept_id_from_department_code(app):
    """The behavior unique to this chatbot: covers_dept_id is an
    internal numeric FK, but the proposed_payload may naturally
    contain a department CODE instead ('CLAIMS' rather than 1) — this
    must be resolved before the row is written, not stored as a
    literal string in an integer FK column."""
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0002",
        "report_title": "Test Report — Code Resolution",
        "covers_dept_id": "CLAIMS",  # deliberately a code, not a numeric id
        "report_period": "2026-05",
        "report_date": "2026-05-01",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    report = repository.get_report_by_ref(dept_id, "RPTTEST0002")
    assert report["covers_dept_id"] == repository.get_dept_id("CLAIMS")


def test_approve_serializes_dict_kpi_summary_to_json_text(app):
    """The other behavior unique to this chatbot: kpi_summary is a
    JSON-text column, but the LLM (or a reviewer) may hand back an
    actual nested dict rather than a pre-serialized string — this must
    be json.dumps()'d before it reaches PyMySQL, which cannot bind a
    dict as a query parameter."""
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0003",
        "report_title": "Test Report — KPI Serialization",
        "covers_dept_id": 2,
        "report_period": "2026-04",
        "report_date": "2026-04-01",
        "kpi_summary": {"volume": 500, "sla_pct": 92.5},  # a real dict, not a string
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    report = repository.get_report_by_ref(dept_id, "RPTTEST0003")
    assert isinstance(report["kpi_summary"], str)
    parsed = json.loads(report["kpi_summary"])
    assert parsed["volume"] == 500
    assert parsed["sla_pct"] == 92.5


def test_approve_rejects_unresolvable_department_code(app):
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0004",
        "report_title": "Test Report — Bad Code",
        "covers_dept_id": "NOT_A_REAL_DEPARTMENT",
        "report_period": "2026-03",
        "report_date": "2026-03-01",
    })

    with pytest.raises(HitlValidationError, match="unrecognized covers_dept_id"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"


def test_approve_updates_existing_report_when_report_ref_matches(app):
    """Approving a HITL task whose report_ref already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO management_reports (dept_id, report_ref, report_title, covers_dept_id, "
                "report_period, report_date) VALUES (%s, 'RPTTEST0005', 'Original Title', 3, '2026-02', "
                "'2026-02-01')",
                (repository.get_dept_id("MANAGEMENT"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0005",
        "report_title": "Updated Title",
        "covers_dept_id": 3,
        "report_period": "2026-02",
        "report_date": "2026-02-01",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    report = repository.get_report_by_ref(dept_id, "RPTTEST0005")
    assert report["report_title"] == "Updated Title"  # updated, not duplicated

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM management_reports WHERE report_ref = 'RPTTEST0005'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0006",
        "report_title": "Test Report",
        "covers_dept_id": 4,
        "report_period": "2026-01",
        "report_date": "2026-01-01",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"report_title": "Edited Title"})
    assert result["status"] == "EDITED"

    report = repository.get_report_by_ref(dept_id, "RPTTEST0006")
    assert report["report_title"] == "Edited Title"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0007",
        # report_title / covers_dept_id / report_period / report_date all missing
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0008",
        "report_title": "Test Report",
        "covers_dept_id": 5,
        "report_period": "2025-12",
        "report_date": "2025-12-01",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_management_reports(app):
    dept_id = repository.get_dept_id("MANAGEMENT")
    task_id = _insert_pending_task(dept_id, {
        "report_ref": "RPTTEST0009",
        "report_title": "Test Report",
        "covers_dept_id": 6,
        "report_period": "2025-11",
        "report_date": "2025-11-01",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert repository.get_report_by_ref(dept_id, "RPTTEST0009") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
