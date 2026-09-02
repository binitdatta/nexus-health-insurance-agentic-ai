import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="billing_records"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="billing-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_invoice_when_invoice_number_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("BILLING")
        task_id = _insert_pending_task(dept_id, {
            "invoice_number": "INVTEST0001",
            "member_id": "MBRTEST01",
            "billing_period": "2026-06",
            "amount_due": "150.00",
            "payment_status": "UNPAID",
            "due_date": "2026-06-15",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        invoice = repository.get_invoice_by_number(dept_id, "INVTEST0001")
        assert invoice is not None
        assert invoice["payment_status"] == "UNPAID"
        assert float(invoice["amount_due"]) == 150.00

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_invoice_when_invoice_number_matches(app):
    """Approving a HITL task whose invoice_number already exists should
    UPDATE that row rather than fail on the unique constraint."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO billing_records (dept_id, invoice_number, member_id, billing_period, amount_due, "
                "payment_status, due_date) VALUES (%s, 'INVTEST0002', 'MBRTEST02', '2026-05', 200.00, "
                "'UNPAID', '2026-05-15')",
                (repository.get_dept_id("BILLING"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0002",
        "member_id": "MBRTEST02",
        "billing_period": "2026-05",
        "amount_due": "200.00",
        "amount_paid": "200.00",
        "payment_status": "PAID",
        "due_date": "2026-05-15",
        "payment_method": "ACH",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    invoice = repository.get_invoice_by_number(dept_id, "INVTEST0002")
    assert invoice["payment_status"] == "PAID"  # updated, not duplicated
    assert invoice["payment_method"] == "ACH"

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM billing_records WHERE invoice_number = 'INVTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0003",
        "member_id": "MBRTEST03",
        "billing_period": "2026-04",
        "amount_due": "300.00",
        "payment_status": "UNPAID",
        "due_date": "2026-04-15",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"payment_status": "WRITTEN_OFF"})
    assert result["status"] == "EDITED"

    invoice = repository.get_invoice_by_number(dept_id, "INVTEST0003")
    assert invoice["payment_status"] == "WRITTEN_OFF"


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0004",
        # member_id / billing_period / amount_due / payment_status / due_date all missing
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0005",
        "member_id": "MBRTEST05",
        "billing_period": "2026-03",
        "amount_due": "100.00",
        "payment_status": "UNPAID",
        "due_date": "2026-03-15",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_billing_records(app):
    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0006",
        "member_id": "MBRTEST06",
        "billing_period": "2026-02",
        "amount_due": "50.00",
        "payment_status": "UNPAID",
        "due_date": "2026-02-15",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_invoice_by_number(dept_id, "INVTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]


def test_approve_normalizes_common_human_typed_date_formats(app):
    """Regression test for a real bug found in live testing: a reviewer
    typing a completely normal US-format date ('12/31/2026') hit an
    unhandled 500 from MySQL, which only accepts 'YYYY-MM-DD' for a
    DATE column. This must now be normalized before it ever reaches
    the database, not just fail with a nicer error message."""
    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0007",
        "member_id": "MBRTEST07",
        "billing_period": "2026-09",
        "amount_due": "600.00",
        "payment_status": "PENDING",
        "due_date": "12/31/2026",  # exactly what a human typed in the real bug report
    })
    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    invoice = repository.get_invoice_by_number(dept_id, "INVTEST0007")
    assert str(invoice["due_date"]) == "2026-12-31"


def test_approve_rejects_unparseable_date_with_specific_message(app):
    """The AI-invented literal '<UNKNOWN>' (or any genuinely unparseable
    date) must be caught with a clear, actionable message naming the
    exact field and value — before it reaches the database, not as a
    raw SQL error."""
    dept_id = repository.get_dept_id("BILLING")
    task_id = _insert_pending_task(dept_id, {
        "invoice_number": "INVTEST0008",
        "member_id": "MBRTEST08",
        "billing_period": "2026-09",
        "amount_due": "600.00",
        "payment_status": "PENDING",
        "due_date": "<UNKNOWN>",
    })
    with pytest.raises(HitlValidationError, match="unrecognized date value"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix
    assert repository.get_invoice_by_number(dept_id, "INVTEST0008") is None
