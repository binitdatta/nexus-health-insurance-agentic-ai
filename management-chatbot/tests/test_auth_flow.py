def test_root_redirects_when_not_logged_in(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_api_returns_401_json_when_not_logged_in(client):
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 401
    assert resp.json["error"] == "Not authenticated"


def test_dev_login_then_chat_page_loads(logged_in_client):
    resp = logged_in_client.get("/")
    assert resp.status_code == 200
    assert b"Management Assistant" in resp.data


def test_wrong_department_rejected(client):
    resp = client.get("/auth/dev-login")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        # Must reassign the top-level "user" key, not mutate the nested
        # dict in place — Flask's session only tracks top-level
        # __setitem__ calls as "modified", so `sess["user"]["department"]
        # = ...` silently fails to persist.
        sess["user"] = {**sess["user"], "department": "MEMBERSVC"}
    resp = client.get("/dashboard")
    assert resp.status_code == 403


def test_logout_clears_session(logged_in_client):
    logged_in_client.get("/auth/logout")
    resp = logged_in_client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
