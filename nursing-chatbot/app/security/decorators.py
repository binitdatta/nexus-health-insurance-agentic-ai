from functools import wraps

from flask import current_app, jsonify, redirect, request, session, url_for


def current_user() -> dict | None:
    return session.get("user")


def login_required(f):
    """
    For page routes: redirects to /auth/login (preserving the requested
    URL) if there's no session. For API/XHR routes (Accept: application/json
    or path under /api/), returns 401 JSON instead of a redirect so the
    frontend JS can react without following a redirect into an HTML page.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        config = current_app.config["APP_CONFIG"]

        if user is None:
            if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
                return jsonify(error="Not authenticated"), 401
            return redirect(url_for("auth.login", next=request.path))

        if str(user.get("department", "")).upper() != config.DEPT_CODE.upper():
            if request.path.startswith("/api/"):
                return jsonify(error=f"Your account is not provisioned for the {config.DEPT_DISPLAY_NAME} chatbot"), 403
            return jsonify(error=f"Your account is not provisioned for the {config.DEPT_DISPLAY_NAME} chatbot"), 403

        return f(*args, **kwargs)

    return wrapper
