from app import repository


def test_dashboard_summary_endpoint_shape(logged_in_client):
    resp = logged_in_client.get("/dashboard/api/summary")
    assert resp.status_code == 200
    body = resp.json
    assert "daily" in body
    assert "totals" in body
    assert "llm" in body["totals"]
    assert "http" in body["totals"]
    assert "recent_llm_calls" in body
    assert "recent_http_calls" in body


def test_dashboard_totals_reflect_real_seed_data(app):
    with app.app_context():
        dept_id = repository.get_dept_id("PRIORAUTH")
        # Real seed data ships 60 llm_call_log / 60 http_call_log rows for
        # 'priorauth-chatbot' (see generate_seed_data.py) — confirm the
        # dashboard's own aggregate query actually sees them.
        totals = repository.dashboard_totals(dept_id, "priorauth-chatbot")
        assert totals["llm"]["call_count"] >= 60
        assert totals["http"]["call_count"] >= 60


def test_dashboard_daily_summary_ordered_ascending(app):
    with app.app_context():
        dept_id = repository.get_dept_id("PRIORAUTH")
        daily = repository.dashboard_daily_summary(dept_id, "priorauth-chatbot", days=14)
        dates = [row["summary_date"] for row in daily]
        assert dates == sorted(dates)
