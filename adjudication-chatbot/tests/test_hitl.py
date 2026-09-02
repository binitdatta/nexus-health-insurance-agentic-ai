import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="adjudication_records"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="adjudication-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_inserts_new_adjudication_record(app):
    with app.app_context():
        dept_id = repository.get_dept_id("ADJUDICATION")
        task_id = _insert_pending_task(dept_id, {
            "claim_number": "CLMTEST0001",
            "adjudicator_id": "ADJTEST01",
            "rule_applied": "Duplicate Check",
            "decision": "APPROVE",
            "adjudicated_date": "2026-05-01",
            "notes": "TEST_ROW_SAFE_TO_DELETE",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        record = repository.get_adjudication_by_id(dept_id, result["entity_ref_id"])
        assert record is not None
        assert record["decision"] == "APPROVE"
        assert record["claim_number"] == "CLMTEST0001"

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_always_inserts_even_when_claim_already_has_a_prior_adjudication(app):
    """The defining behavioral difference from every other department
    chatbot: claim_number is NOT unique here, and a claim being
    re-adjudicated is a genuinely new event, not a correction to the
    old one. Approving a HITL task for a claim that already has an
    adjudication record must insert a second row, not overwrite the
    first — the audit trail of both decisions must survive."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adjudication_records (dept_id, claim_number, adjudicator_id, rule_applied, "
                "decision, adjudicated_date, notes) VALUES (%s, 'CLMTEST0002', 'ADJTEST02', 'Timely Filing', "
                "'DENY', '2026-04-01', 'TEST_ROW_SAFE_TO_DELETE')",
                (repository.get_dept_id("ADJUDICATION"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("ADJUDICATION")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0002",
        "adjudicator_id": "ADJTEST03",
        "rule_applied": "Timely Filing",
        "decision": "APPROVE",
        "adjudicated_date": "2026-05-10",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision, adjudicated_date FROM adjudication_records "
                "WHERE claim_number = 'CLMTEST0002' ORDER BY adjudication_id"
            )
            rows = cur.fetchall()
            assert len(rows) == 2  # both the original DENY and the new APPROVE survive
            assert rows[0]["decision"] == "DENY"
            assert rows[1]["decision"] == "APPROVE"
    finally:
        conn.close()

    # And retrieve_node's "latest adjudication" lookup should surface the new one
    latest = repository.get_latest_adjudication_by_claim(dept_id, "CLMTEST0002")
    assert latest["decision"] == "APPROVE"


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("ADJUDICATION")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0003",
        "adjudicator_id": "ADJTEST04",
        "rule_applied": "Fee Schedule Cap",
        "decision": "PEND",
        "adjudicated_date": "2026-03-01",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"decision": "ADJUST", "adjustment_amount": "125.50"})
    assert result["status"] == "EDITED"

    record = repository.get_adjudication_by_id(dept_id, result["entity_ref_id"])
    assert record["decision"] == "ADJUST"
    assert float(record["adjustment_amount"]) == 125.50


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("ADJUDICATION")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0004",
        # adjudicator_id / rule_applied / decision / adjudicated_date all missing
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("ADJUDICATION")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0005",
        "adjudicator_id": "ADJTEST05",
        "rule_applied": "COB Rule",
        "decision": "DENY",
        "adjudicated_date": "2026-02-01",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_adjudication_records(app):
    dept_id = repository.get_dept_id("ADJUDICATION")
    task_id = _insert_pending_task(dept_id, {
        "claim_number": "CLMTEST0006",
        "adjudicator_id": "ADJTEST06",
        "rule_applied": "Medical Necessity",
        "decision": "APPROVE",
        "adjudicated_date": "2026-01-15",
        "notes": "TEST_ROW_SAFE_TO_DELETE",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"

    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM adjudication_records WHERE claim_number = 'CLMTEST0006'")
            assert cur.fetchone()["n"] == 0
    finally:
        conn.close()


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
