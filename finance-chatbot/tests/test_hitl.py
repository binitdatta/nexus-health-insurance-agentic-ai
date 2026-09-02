import pytest

from app import repository
from app.repository import HitlValidationError


def _insert_pending_task(dept_id, payload, entity_type="finance_transactions"):
    return repository.insert_hitl_task(
        dept_id=dept_id,
        chatbot_source="finance-chatbot-test",
        session_id="test-session-hitl",
        requested_by_user_id=None,
        entity_type=entity_type,
        proposed_payload=payload,
        ai_rationale="test rationale",
    )


def test_approve_creates_new_transaction_when_txn_reference_not_found(app):
    with app.app_context():
        dept_id = repository.get_dept_id("FINANCE")
        task_id = _insert_pending_task(dept_id, {
            "txn_reference": "TXNTEST0001",
            "txn_type": "VENDOR_PAYMENT",
            "amount": "-1500.00",
            "txn_date": "2026-06-01",
            "gl_account": "GL-4999",
        })

        result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
        assert result["status"] == "APPROVED"
        assert result["entity_ref_id"] > 0

        txn = repository.get_transaction_by_reference(dept_id, "TXNTEST0001")
        assert txn is not None
        assert txn["txn_type"] == "VENDOR_PAYMENT"
        assert float(txn["amount"]) == -1500.00

        task = repository.get_hitl_task(task_id)
        assert task["status"] == "APPROVED"
        assert task["entity_ref_id"] == result["entity_ref_id"]


def test_approve_updates_existing_transaction_when_txn_reference_matches(app):
    """Approving a HITL task whose txn_reference already exists should
    UPDATE that row rather than fail on the unique constraint — unlike
    Adjudication, txn_reference IS unique on this table."""
    from app import extensions
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO finance_transactions (dept_id, txn_reference, txn_type, amount, txn_date, "
                "gl_account) VALUES (%s, 'TXNTEST0002', 'ACCRUAL', 1000.00, '2026-05-01', 'GL-4100')",
                (repository.get_dept_id("FINANCE"),),
            )
    finally:
        conn.close()

    dept_id = repository.get_dept_id("FINANCE")
    task_id = _insert_pending_task(dept_id, {
        "txn_reference": "TXNTEST0002",
        "txn_type": "ADJUSTMENT",
        "amount": "1250.00",
        "txn_date": "2026-05-01",
        "gl_account": "GL-4100",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None)
    assert result["status"] == "APPROVED"

    txn = repository.get_transaction_by_reference(dept_id, "TXNTEST0002")
    assert txn["txn_type"] == "ADJUSTMENT"  # updated, not duplicated
    assert float(txn["amount"]) == 1250.00

    from app import extensions as ext
    conn = ext.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM finance_transactions WHERE txn_reference = 'TXNTEST0002'")
            assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_approve_with_reviewer_edit_marks_edited_status(app):
    dept_id = repository.get_dept_id("FINANCE")
    task_id = _insert_pending_task(dept_id, {
        "txn_reference": "TXNTEST0003",
        "txn_type": "ACCRUAL",
        "amount": "500.00",
        "txn_date": "2026-04-01",
        "gl_account": "GL-4200",
    })

    result = repository.approve_hitl_task(task_id, reviewer_user_id=None, edited_payload={"amount": "750.00"})
    assert result["status"] == "EDITED"

    txn = repository.get_transaction_by_reference(dept_id, "TXNTEST0003")
    assert float(txn["amount"]) == 750.00


def test_approve_rejects_missing_required_fields(app):
    dept_id = repository.get_dept_id("FINANCE")
    task_id = _insert_pending_task(dept_id, {
        "txn_reference": "TXNTEST0004",
        # txn_type / amount / txn_date / gl_account all missing
    })

    with pytest.raises(HitlValidationError, match="missing required field"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)

    task = repository.get_hitl_task(task_id)
    assert task["status"] == "PENDING"  # left untouched for the reviewer to fix


def test_approve_twice_rejected(app):
    dept_id = repository.get_dept_id("FINANCE")
    task_id = _insert_pending_task(dept_id, {
        "txn_reference": "TXNTEST0005",
        "txn_type": "PREMIUM_RECEIPT",
        "amount": "300.00",
        "txn_date": "2026-03-01",
        "gl_account": "GL-4300",
    })
    repository.approve_hitl_task(task_id, reviewer_user_id=None)
    with pytest.raises(HitlValidationError, match="already"):
        repository.approve_hitl_task(task_id, reviewer_user_id=None)


def test_reject_marks_status_and_does_not_touch_finance_transactions(app):
    dept_id = repository.get_dept_id("FINANCE")
    task_id = _insert_pending_task(dept_id, {
        "txn_reference": "TXNTEST0006",
        "txn_type": "CLAIM_PAYOUT",
        "amount": "-800.00",
        "txn_date": "2026-02-01",
        "gl_account": "GL-4400",
    })
    repository.reject_hitl_task(task_id, reviewer_user_id=None, review_notes="Not enough info")
    task = repository.get_hitl_task(task_id)
    assert task["status"] == "REJECTED"
    assert task["review_notes"] == "Not enough info"
    assert repository.get_transaction_by_reference(dept_id, "TXNTEST0006") is None


def test_hitl_api_endpoints_via_http(app, logged_in_client):
    resp = logged_in_client.post("/hitl/api/tasks/999999/approve", json={})
    assert resp.status_code == 400
    assert "not found" in resp.json["error"]
