"""
All SQL the Call Center chatbot needs, hand-written against the
health_ai_platform schema (owned by schema.sql — nothing here issues
DDL). Grouped by concern: identity, call log retrieval, knowledge_docs
RAG search, the HITL queue (including the commit-on-approve step), the
http_call_log audit trail, and the cost/call dashboard aggregates.
"""
import json
from datetime import date, datetime
from typing import Optional

from . import extensions

_dept_id_cache: dict[str, int] = {}

REQUIRED_CALL_FIELDS = ["call_reference", "member_id", "agent_id", "call_datetime", "call_type", "resolution_status"]
CALL_COLUMNS = [
    "call_reference", "member_id", "agent_id", "call_datetime", "call_type", "duration_seconds",
    "resolution_status", "call_notes", "csat_score",
]


# --- Identity -----------------------------------------------------------

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
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM app_users WHERE keycloak_sub = %s", (keycloak_sub,))
            row = cur.fetchone()
            if row:
                return row["user_id"]
            cur.execute(
                "INSERT INTO app_users (keycloak_sub, username, email, dept_id, is_active) VALUES (%s, %s, %s, %s, 1)",
                (keycloak_sub, username or keycloak_sub, email or None, dept_id),
            )
            return cur.lastrowid
    finally:
        conn.close()


# --- Call log retrieval (for LangGraph's retrieve node) --------------------
# Note: call_datetime is a full DATETIME, not just DATE like every other
# department chatbot's date filters — date_to must compare against the
# START of the *next* day (DATE_ADD ... INTERVAL 1 DAY), not <= date_to
# directly, or same-day calls with a time component after midnight would
# be silently excluded. csat_score is also new: a nullable 1-5 rating,
# the first aggregatable numeric metric column in this platform (every
# other chatbot's numeric columns were currency, not a rating scale).

def search_calls(
    dept_id: int, *, call_reference: str = None, member_id: str = None, agent_id: str = None,
    call_type: str = None, resolution_status: str = None, date_from: str = None, date_to: str = None,
    limit: int = 25,
) -> list[dict]:
    clauses = ["dept_id = %s"]
    params: list = [dept_id]
    if call_reference:
        clauses.append("call_reference = %s")
        params.append(call_reference)
    if member_id:
        clauses.append("member_id = %s")
        params.append(member_id)
    if agent_id:
        clauses.append("agent_id = %s")
        params.append(agent_id)
    if call_type:
        clauses.append("call_type = %s")
        params.append(call_type.upper())
    if resolution_status:
        clauses.append("resolution_status = %s")
        params.append(resolution_status.upper())
    if date_from:
        clauses.append("call_datetime >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("call_datetime < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(date_to)

    sql = (
        "SELECT call_id, call_reference, member_id, agent_id, call_datetime, call_type, duration_seconds, "
        "resolution_status, call_notes, csat_score FROM call_center_logs WHERE " + " AND ".join(clauses) +
        " ORDER BY call_datetime DESC LIMIT %s"
    )
    params.append(limit)

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def aggregate_calls_by_type(dept_id: int, date_from: str = None, date_to: str = None) -> list[dict]:
    clauses = ["dept_id = %s"]
    params: list = [dept_id]
    if date_from:
        clauses.append("call_datetime >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("call_datetime < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(date_to)

    sql = (
        "SELECT call_type, COUNT(*) AS call_count, ROUND(AVG(csat_score), 2) AS avg_csat "
        "FROM call_center_logs WHERE " + " AND ".join(clauses) +
        " GROUP BY call_type ORDER BY call_count DESC"
    )
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def aggregate_calls_by_resolution(dept_id: int) -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT resolution_status, call_type, COUNT(*) AS call_count, ROUND(AVG(csat_score), 2) AS avg_csat "
                "FROM call_center_logs WHERE dept_id = %s "
                "GROUP BY resolution_status, call_type ORDER BY resolution_status, call_count DESC",
                (dept_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_call_by_reference(dept_id: int, call_reference: str) -> Optional[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM call_center_logs WHERE dept_id = %s AND call_reference = %s",
                (dept_id, call_reference),
            )
            return cur.fetchone()
    finally:
        conn.close()


# --- knowledge_docs RAG search --------------------------------------------

def search_knowledge_docs(dept_id: int, query_text: str, limit: int = 5) -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT doc_id, title, doc_type, content, tags,
                       MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS relevance
                FROM knowledge_docs
                WHERE dept_id = %s AND is_active = 1
                  AND MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE)
                ORDER BY relevance DESC
                LIMIT %s
                """,
                (query_text, dept_id, query_text, limit),
            )
            rows = cur.fetchall()
            if rows:
                return rows
            # FULLTEXT natural-language mode returns nothing for very short/odd
            # queries (e.g. a single common word) — fall back to latest docs
            # so the chat still has *something* department-specific to ground on.
            cur.execute(
                "SELECT doc_id, title, doc_type, content, tags, 0 AS relevance FROM knowledge_docs "
                "WHERE dept_id = %s AND is_active = 1 ORDER BY updated_at DESC LIMIT %s",
                (dept_id, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


# --- http_call_log --------------------------------------------------------

def insert_http_call_log(entry: dict) -> int:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO http_call_log (
                    request_id, dept_id, user_id, chatbot_source, session_id, http_method, endpoint,
                    target_service, request_payload, response_payload, response_status, latency_ms, client_ip
                ) VALUES (
                    %(request_id)s, %(dept_id)s, %(user_id)s, %(chatbot_source)s, %(session_id)s,
                    %(http_method)s, %(endpoint)s, %(target_service)s, %(request_payload)s,
                    %(response_payload)s, %(response_status)s, %(latency_ms)s, %(client_ip)s
                )
                """,
                {
                    **entry,
                    "request_payload": json.dumps(entry.get("request_payload") or {}, default=str),
                    "response_payload": json.dumps(entry.get("response_payload") or {}, default=str),
                },
            )
            return cur.lastrowid
    finally:
        conn.close()


# --- HITL queue -------------------------------------------------------------

def insert_hitl_task(
    *, dept_id: int, chatbot_source: str, session_id: str, requested_by_user_id: Optional[int],
    entity_type: str, proposed_payload: dict, ai_rationale: str,
) -> int:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hitl_task_queue (
                    dept_id, chatbot_source, session_id, requested_by_user_id, task_type, entity_type,
                    proposed_payload, ai_rationale, status
                ) VALUES (%s, %s, %s, %s, 'CREATE', %s, %s, %s, 'PENDING')
                """,
                (dept_id, chatbot_source, session_id, requested_by_user_id, entity_type,
                 json.dumps(proposed_payload, default=str), ai_rationale),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_hitl_task(task_id: int) -> Optional[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hitl_task_queue WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
            if row:
                row["proposed_payload"] = json.loads(row["proposed_payload"]) if row["proposed_payload"] else {}
                if row.get("original_payload"):
                    row["original_payload"] = json.loads(row["original_payload"])
            return row
    finally:
        conn.close()


def list_hitl_tasks(dept_id: int, status: Optional[str] = None, limit: int = 50) -> list[dict]:
    clauses = ["dept_id = %s"]
    params: list = [dept_id]
    if status:
        clauses.append("status = %s")
        params.append(status.upper())
    sql = (
        "SELECT * FROM hitl_task_queue WHERE " + " AND ".join(clauses) +
        " ORDER BY created_at DESC LIMIT %s"
    )
    params.append(limit)
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            for row in rows:
                row["proposed_payload"] = json.loads(row["proposed_payload"]) if row["proposed_payload"] else {}
            return rows
    finally:
        conn.close()


class HitlValidationError(Exception):
    pass


# call_datetime is a DATETIME column, not DATE (see repository.py's
# module docstring — the first non-DATE date column in this platform),
# so it needs a distinct set of accepted input formats and a different
# output format ('YYYY-MM-DD HH:MM:SS') than every other chatbot's
# plain DATE columns.
_DATETIME_FIELDS = ["call_datetime"]
_DATETIME_INPUT_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p",
    "%Y-%m-%d",  # date only — normalized to midnight
]


def _normalize_date_fields(payload: dict) -> dict:
    normalized = dict(payload)
    for field in _DATETIME_FIELDS:
        value = normalized.get(field)
        if not value or not isinstance(value, str):
            continue
        parsed = None
        for fmt in _DATETIME_INPUT_FORMATS:
            try:
                parsed = datetime.strptime(value.strip(), fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise HitlValidationError(
                f"Cannot approve — '{field}' has an unrecognized date/time value '{value}'. "
                f"Use YYYY-MM-DD HH:MM:SS (e.g. 2026-12-31 14:30:00)."
            )
        normalized[field] = parsed.strftime("%Y-%m-%d %H:%M:%S")
    return normalized


def _clean_call_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k in CALL_COLUMNS}


def _validate_call_payload(payload: dict) -> None:
    missing = [f for f in REQUIRED_CALL_FIELDS if not payload.get(f)]
    if missing:
        raise HitlValidationError(f"Cannot approve — missing required field(s): {', '.join(missing)}")


def approve_hitl_task(task_id: int, reviewer_user_id: Optional[int], edited_payload: Optional[dict] = None,
                       review_notes: Optional[str] = None) -> dict:
    """
    Commits the (possibly reviewer-edited) proposed record to its target
    domain table and marks the task APPROVED (or EDITED if the reviewer
    changed anything). Returns {"entity_ref_id": ..., "status": ...}.
    """
    task = get_hitl_task(task_id)
    if task is None:
        raise HitlValidationError(f"HITL task {task_id} not found")
    if task["status"] != "PENDING":
        raise HitlValidationError(f"HITL task {task_id} is already {task['status']}")

    final_payload = dict(task["proposed_payload"])
    was_edited = False
    if edited_payload:
        was_edited = edited_payload != task["proposed_payload"]
        final_payload.update(edited_payload)

    if task["entity_type"] != "call_center_logs":
        raise HitlValidationError(f"Unsupported entity_type for this chatbot: {task['entity_type']}")

    clean_payload = _clean_call_payload(final_payload)
    clean_payload = _normalize_date_fields(clean_payload)
    _validate_call_payload(clean_payload)

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            existing = None
            cur.execute(
                "SELECT call_id FROM call_center_logs WHERE dept_id = %s AND call_reference = %s",
                (task["dept_id"], clean_payload["call_reference"]),
            )
            existing = cur.fetchone()

            if existing:
                set_clause = ", ".join(f"{col} = %s" for col in clean_payload if col != "call_reference")
                values = [v for k, v in clean_payload.items() if k != "call_reference"]
                cur.execute(
                    f"UPDATE call_center_logs SET {set_clause} WHERE call_id = %s",
                    values + [existing["call_id"]],
                )
                entity_ref_id = existing["call_id"]
            else:
                cols = ["dept_id"] + list(clean_payload.keys())
                placeholders = ["%s"] * len(cols)
                cur.execute(
                    f"INSERT INTO call_center_logs ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
                    [task["dept_id"]] + list(clean_payload.values()),
                )
                entity_ref_id = cur.lastrowid

            new_status = "EDITED" if was_edited else "APPROVED"
            cur.execute(
                """
                UPDATE hitl_task_queue
                SET status = %s, reviewer_user_id = %s, review_notes = %s, reviewed_at = NOW(),
                    entity_ref_id = %s, original_payload = %s
                WHERE task_id = %s
                """,
                (new_status, reviewer_user_id, review_notes, entity_ref_id,
                 json.dumps(task["proposed_payload"], default=str) if was_edited else None, task_id),
            )
        return {"entity_ref_id": entity_ref_id, "status": new_status}
    finally:
        conn.close()


def reject_hitl_task(task_id: int, reviewer_user_id: Optional[int], review_notes: Optional[str]) -> None:
    task = get_hitl_task(task_id)
    if task is None:
        raise HitlValidationError(f"HITL task {task_id} not found")
    if task["status"] != "PENDING":
        raise HitlValidationError(f"HITL task {task_id} is already {task['status']}")

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hitl_task_queue SET status = 'REJECTED', reviewer_user_id = %s, review_notes = %s, "
                "reviewed_at = NOW() WHERE task_id = %s",
                (reviewer_user_id, review_notes, task_id),
            )
    finally:
        conn.close()


# --- Dashboard aggregates ----------------------------------------------------

def dashboard_daily_summary(dept_id: int, chatbot_source: str, days: int = 14) -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT summary_date, total_llm_calls, total_http_calls, total_tokens, total_cost_usd,
                       avg_llm_latency_ms, avg_http_latency_ms, error_count
                FROM cost_summary_daily
                WHERE dept_id = %s AND chatbot_source = %s
                ORDER BY summary_date ASC
                LIMIT %s
                """,
                (dept_id, chatbot_source, days),
            )
            return cur.fetchall()
    finally:
        conn.close()


def dashboard_totals(dept_id: int, chatbot_source: str) -> dict:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS call_count, COALESCE(SUM(total_tokens),0) AS total_tokens,
                       COALESCE(SUM(total_cost_usd),0) AS total_cost_usd,
                       COALESCE(AVG(latency_ms),0) AS avg_latency_ms,
                       SUM(CASE WHEN call_status = 'ERROR' THEN 1 ELSE 0 END) AS error_count
                FROM llm_call_log WHERE dept_id = %s AND chatbot_source = %s
                """,
                (dept_id, chatbot_source),
            )
            llm_totals = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) AS call_count, COALESCE(AVG(latency_ms),0) AS avg_latency_ms "
                "FROM http_call_log WHERE dept_id = %s AND chatbot_source = %s",
                (dept_id, chatbot_source),
            )
            http_totals = cur.fetchone()
            return {"llm": llm_totals, "http": http_totals}
    finally:
        conn.close()


def dashboard_recent_llm_calls(dept_id: int, chatbot_source: str, limit: int = 20) -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id, operation, model_name, call_status, http_status, prompt_tokens,
                       completion_tokens, total_cost_usd, latency_ms, created_at
                FROM llm_call_log WHERE dept_id = %s AND chatbot_source = %s
                ORDER BY created_at DESC LIMIT %s
                """,
                (dept_id, chatbot_source, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def dashboard_recent_http_calls(dept_id: int, chatbot_source: str, limit: int = 20) -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_id, http_method, endpoint, target_service, response_status, latency_ms, created_at
                FROM http_call_log WHERE dept_id = %s AND chatbot_source = %s
                ORDER BY created_at DESC LIMIT %s
                """,
                (dept_id, chatbot_source, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()
