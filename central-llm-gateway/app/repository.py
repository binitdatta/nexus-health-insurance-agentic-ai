"""
Hand-written SQL against the health_ai_platform schema (owned by
schema.sql — this module never creates or alters tables). Every
function opens a pooled connection, does exactly the SQL it needs, and
returns plain dicts.
"""
import json
from typing import Optional

from . import extensions

_dept_id_cache: dict[str, int] = {}


def get_dept_id(dept_code: str) -> Optional[int]:
    dept_code = dept_code.upper()
    if dept_code in _dept_id_cache:
        return _dept_id_cache[dept_code]
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT dept_id FROM departments WHERE dept_code = %s AND is_active = 1", (dept_code,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row:
        _dept_id_cache[dept_code] = row["dept_id"]
        return row["dept_id"]
    return None


def get_or_create_user(keycloak_sub: str, dept_id: int, username: str = "", email: str = "") -> int:
    """
    Look up the app_users row for this Keycloak subject; create a
    minimal one on first sight so llm_call_log/http_call_log always
    have a valid user_id to reference. Chatbots/IdP sync jobs are
    expected to fill in full profile fields separately — this is just
    enough to satisfy the FK.
    """
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM app_users WHERE keycloak_sub = %s", (keycloak_sub,))
            row = cur.fetchone()
            if row:
                return row["user_id"]
            cur.execute(
                """INSERT INTO app_users (keycloak_sub, username, email, dept_id, is_active)
                   VALUES (%s, %s, %s, %s, 1)""",
                (keycloak_sub, username or keycloak_sub, email or None, dept_id),
            )
            return cur.lastrowid
    finally:
        conn.close()


def insert_llm_call_log(entry: dict) -> int:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_call_log (
                    request_id, dept_id, user_id, chatbot_source, session_id, model_name, operation,
                    endpoint, intent_detected, request_payload, response_payload, prompt_tokens,
                    completion_tokens, total_tokens, input_cost_usd, output_cost_usd, total_cost_usd,
                    latency_ms, http_status, call_status, error_message
                ) VALUES (
                    %(request_id)s, %(dept_id)s, %(user_id)s, %(chatbot_source)s, %(session_id)s,
                    %(model_name)s, %(operation)s, %(endpoint)s, %(intent_detected)s,
                    %(request_payload)s, %(response_payload)s, %(prompt_tokens)s, %(completion_tokens)s,
                    %(total_tokens)s, %(input_cost_usd)s, %(output_cost_usd)s, %(total_cost_usd)s,
                    %(latency_ms)s, %(http_status)s, %(call_status)s, %(error_message)s
                )
                """,
                {
                    **entry,
                    "request_payload": json.dumps(entry.get("request_payload") or {}),
                    "response_payload": json.dumps(entry.get("response_payload") or {}),
                },
            )
            return cur.lastrowid
    finally:
        conn.close()
