import pytest

from app import create_app
from app.config import Config


class TestConfig(Config):
    ENV = "development"
    DEBUG = True
    SECRET_KEY = "test-secret-key"
    DEV_BYPASS_AUTH = True
    DEPT_CODE = "CLAIMS"
    CHATBOT_SOURCE = "claims-chatbot-test"  # isolate test rows from real seed data by chatbot_source


@pytest.fixture()
def app():
    flask_app = create_app(TestConfig)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def logged_in_client(client):
    client.get("/auth/dev-login")
    return client


@pytest.fixture()
def canned_gateway_responses(monkeypatch):
    """
    Patches app.gateway_client._post so tests control exactly what each
    Gateway operation returns, without a real Anthropic key or a running
    Gateway process. Configure per-test via the returned dict, keyed by
    the operation name in the URL path ('intent' | 'respond' | 'hitl-draft').
    """
    canned = {}

    def fake_post(path, payload):
        for key, response in canned.items():
            if path.endswith(key):
                if callable(response):
                    return response(payload)
                return response
        raise AssertionError(f"No canned response configured for path {path}")

    monkeypatch.setattr("app.gateway_client._post", fake_post)
    return canned


@pytest.fixture(autouse=True)
def _cleanup_test_rows():
    """Removes anything this test run wrote under the test chatbot_source
    so re-runs stay deterministic and the real seed data is untouched.
    No-ops for tests that never touch the DB (the pool is only created
    lazily by the `app` fixture)."""
    yield
    from app import extensions

    if extensions._pool is None:
        return

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hitl_task_queue WHERE chatbot_source = %s", (TestConfig.CHATBOT_SOURCE,))
            cur.execute("DELETE FROM http_call_log WHERE chatbot_source = %s", (TestConfig.CHATBOT_SOURCE,))
            cur.execute("DELETE FROM llm_call_log WHERE chatbot_source = %s", (TestConfig.CHATBOT_SOURCE,))
            cur.execute("DELETE FROM claims WHERE notes = 'TEST_ROW_SAFE_TO_DELETE'")
    finally:
        conn.close()
