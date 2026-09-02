"""
All SQL the Management chatbot needs, hand-written against the
health_ai_platform schema (owned by schema.sql — nothing here issues
DDL). Grouped by concern: identity, cross-department report
retrieval, knowledge_docs RAG search, the HITL queue (including the
commit-on-approve step), the http_call_log audit trail, and the
cost/call dashboard aggregates.
"""
import json
from datetime import date, datetime
from typing import Optional

from . import extensions

_dept_id_cache: dict[str, int] = {}
_dept_code_by_id_cache: dict[int, str] = {}

REQUIRED_REPORT_FIELDS = ["report_ref", "report_title", "covers_dept_id", "report_period", "report_date"]
REPORT_COLUMNS = [
    "report_ref", "report_title", "covers_dept_id", "report_period", "kpi_summary", "prepared_by", "report_date",
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


# --- Department reference (Management is the first chatbot whose data
# genuinely cross-references OTHER departments — covers_dept_id is a
# foreign key into `departments` naming which department a report is
# about. Neither a human nor the LLM can reasonably guess the right
# numeric dept_id from a department name alone, so this reference list
# is included directly in retrieved_context whenever a create/update
# draft needs to pick one — see retrieve_node.) ------------------------

def list_department_reference() -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT dept_id, dept_code, dept_name FROM departments WHERE is_active = 1 ORDER BY dept_id")
            rows = cur.fetchall()
            for row in rows:
                _dept_code_by_id_cache[row["dept_id"]] = row["dept_code"]
            return rows
    finally:
        conn.close()


def dept_code_for_id(dept_id: Optional[int]) -> Optional[str]:
    if dept_id is None:
        return None
    if dept_id not in _dept_code_by_id_cache:
        list_department_reference()
    return _dept_code_by_id_cache.get(dept_id)


# --- Report retrieval (for LangGraph's retrieve node) --------------------
# Note: kpi_summary is stored as a JSON string (schema.sql: LONGTEXT,
# not a native JSON column) — PyMySQL returns it as plain text on read,
# and MySQL 8's JSON_EXTRACT() works directly against that text without
# needing a true JSON column type, which the aggregate below relies on.

def search_reports(
    dept_id: int, *, report_ref: str = None, covers_dept_id: int = None, report_period: str = None,
    date_from: str = None, date_to: str = None, limit: int = 25,
) -> list[dict]:
    clauses = ["dept_id = %s"]
    params: list = [dept_id]
    if report_ref:
        clauses.append("report_ref = %s")
        params.append(report_ref)
    if covers_dept_id:
        clauses.append("covers_dept_id = %s")
        params.append(covers_dept_id)
    if report_period:
        clauses.append("report_period = %s")
        params.append(report_period)
    if date_from:
        clauses.append("report_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("report_date <= %s")
        params.append(date_to)

    sql = (
        "SELECT report_id, report_ref, report_title, covers_dept_id, report_period, kpi_summary, "
        "prepared_by, report_date FROM management_reports WHERE " + " AND ".join(clauses) +
        " ORDER BY report_date DESC LIMIT %s"
    )
    params.append(limit)

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def aggregate_reports_by_covered_department(dept_id: int) -> list[dict]:
    """Count of reports and average SLA% per department they cover —
    the average is pulled straight out of the kpi_summary JSON text
    via JSON_EXTRACT, not a separate structured column."""
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.dept_code, d.dept_name, COUNT(*) AS report_count,
                       ROUND(AVG(CAST(JSON_EXTRACT(mr.kpi_summary, '$.sla_pct') AS DECIMAL(5,2))), 2) AS avg_sla_pct
                FROM management_reports mr
                JOIN departments d ON d.dept_id = mr.covers_dept_id
                WHERE mr.dept_id = %s
                GROUP BY d.dept_code, d.dept_name
                ORDER BY report_count DESC
                """,
                (dept_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_report_by_ref(dept_id: int, report_ref: str) -> Optional[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM management_reports WHERE dept_id = %s AND report_ref = %s", (dept_id, report_ref)
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


def _clean_report_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k in REPORT_COLUMNS}


_DATE_INPUT_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%y"]


def _normalize_report_payload(payload: dict) -> dict:
    """
    Three normalizations specific to this table:

    1. covers_dept_id: the LLM (or a reviewer typing into the HITL
       form) may naturally supply a department CODE ("CLAIMS") rather
       than the internal numeric dept_id the column actually stores —
       retrieve_node includes the department reference list in context
       specifically so the LLM has the mapping, but a human reviewer
       editing the field by hand has no reason to know the numeric ID
       either. If the value isn't already an int (or a numeric
       string), resolve it as a department code.
    2. kpi_summary: the column is JSON text (schema.sql: LONGTEXT, not
       a native JSON type), but a well-behaved LLM tool-use response —
       or a reviewer's edited form — may hand this back as an actual
       nested dict/list rather than a pre-serialized string. PyMySQL
       can't bind a dict as a query parameter, so it must be
       json.dumps()'d before it ever reaches the SQL layer.
    3. report_date: a real MySQL DATE column — a reviewer typing a
       normal date format (e.g. '12/31/2026') would otherwise crash
       with a raw SQL error, since MySQL's DATE type only accepts
       'YYYY-MM-DD'. Same normalization as every other chatbot's date
       fields, just folded into this table's existing normalize step.
    """
    normalized = dict(payload)
    covers = normalized.get("covers_dept_id")
    if covers is not None and not (isinstance(covers, int) or (isinstance(covers, str) and covers.isdigit())):
        resolved = get_dept_id(str(covers))
        if resolved is None:
            raise HitlValidationError(f"Cannot approve — unrecognized covers_dept_id/department code: {covers!r}")
        normalized["covers_dept_id"] = resolved
    elif isinstance(covers, str) and covers.isdigit():
        normalized["covers_dept_id"] = int(covers)

    kpi = normalized.get("kpi_summary")
    if isinstance(kpi, (dict, list)):
        normalized["kpi_summary"] = json.dumps(kpi)

    report_date = normalized.get("report_date")
    if report_date and isinstance(report_date, str):
        parsed = None
        for fmt in _DATE_INPUT_FORMATS:
            try:
                parsed = datetime.strptime(report_date.strip(), fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise HitlValidationError(
                f"Cannot approve — 'report_date' has an unrecognized date value '{report_date}'. "
                f"Use YYYY-MM-DD (e.g. 2026-12-31)."
            )
        normalized["report_date"] = parsed.isoformat()

    return normalized


def _validate_report_payload(payload: dict) -> None:
    missing = [f for f in REQUIRED_REPORT_FIELDS if not payload.get(f)]
    if missing:
        raise HitlValidationError(f"Cannot approve — missing required field(s): {', '.join(missing)}")


def approve_hitl_task(task_id: int, reviewer_user_id: Optional[int], edited_payload: Optional[dict] = None,
                       review_notes: Optional[str] = None) -> dict:
    """
    Commits the (possibly reviewer-edited) proposed record to its target
    domain table and marks the task APPROVED (or EDITED if the reviewer
    changed anything). Returns {"entity_ref_id": ..., "status": ...}.

    Normal update-vs-insert pattern (report_ref IS unique on this
    table, unlike Adjudication's claim_number) — see
    _normalize_report_payload for the two things about this table's
    columns (a cross-department FK and a JSON-text column) that no
    other chatbot in this platform needs to handle.
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

    if task["entity_type"] != "management_reports":
        raise HitlValidationError(f"Unsupported entity_type for this chatbot: {task['entity_type']}")

    clean_payload = _clean_report_payload(final_payload)
    _validate_report_payload(clean_payload)
    clean_payload = _normalize_report_payload(clean_payload)

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            existing = None
            cur.execute(
                "SELECT report_id FROM management_reports WHERE dept_id = %s AND report_ref = %s",
                (task["dept_id"], clean_payload["report_ref"]),
            )
            existing = cur.fetchone()

            if existing:
                set_clause = ", ".join(f"{col} = %s" for col in clean_payload if col != "report_ref")
                values = [v for k, v in clean_payload.items() if k != "report_ref"]
                cur.execute(
                    f"UPDATE management_reports SET {set_clause} WHERE report_id = %s",
                    values + [existing["report_id"]],
                )
                entity_ref_id = existing["report_id"]
            else:
                cols = ["dept_id"] + list(clean_payload.keys())
                placeholders = ["%s"] * len(cols)
                cur.execute(
                    f"INSERT INTO management_reports ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
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
