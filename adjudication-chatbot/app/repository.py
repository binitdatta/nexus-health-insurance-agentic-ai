"""
All SQL the Adjudication chatbot needs, hand-written against the
health_ai_platform schema (owned by schema.sql — nothing here issues
DDL). Grouped by concern: identity, adjudication record retrieval,
knowledge_docs RAG search, the HITL queue (including the
commit-on-approve step), the http_call_log audit trail, and the
cost/call dashboard aggregates.
"""
import json
from datetime import date, datetime
from typing import Optional

from . import extensions

_dept_id_cache: dict[str, int] = {}

REQUIRED_ADJUDICATION_FIELDS = ["claim_number", "adjudicator_id", "rule_applied", "decision", "adjudicated_date"]
ADJUDICATION_COLUMNS = [
    "claim_number", "adjudicator_id", "rule_applied", "decision", "adjustment_amount",
    "adjudicated_date", "notes",
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


# --- Adjudication record retrieval (for LangGraph's retrieve node) -------
# Note: unlike claims/pa_number/case_number/invoice_number/provider_code
# in every other department chatbot, `claim_number` on adjudication_records
# is NOT unique — a single claim can legitimately be adjudicated more than
# once (original decision, then a correction or re-adjudication after
# appeal). There is no natural unique business key on this table at all;
# the only true identity is the auto-increment adjudication_id. See the
# HITL commit section below for how this changes the update-vs-insert
# logic compared to every other chatbot in this platform.

def search_adjudications(
    dept_id: int, *, claim_number: str = None, adjudicator_id: str = None, decision: str = None,
    rule_applied: str = None, date_from: str = None, date_to: str = None, limit: int = 25,
) -> list[dict]:
    clauses = ["dept_id = %s"]
    params: list = [dept_id]
    if claim_number:
        clauses.append("claim_number = %s")
        params.append(claim_number)
    if adjudicator_id:
        clauses.append("adjudicator_id = %s")
        params.append(adjudicator_id)
    if decision:
        clauses.append("decision = %s")
        params.append(decision.upper())
    if rule_applied:
        clauses.append("rule_applied = %s")
        params.append(rule_applied)
    if date_from:
        clauses.append("adjudicated_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("adjudicated_date <= %s")
        params.append(date_to)

    sql = (
        "SELECT adjudication_id, claim_number, adjudicator_id, rule_applied, decision, adjustment_amount, "
        "adjudicated_date, notes FROM adjudication_records WHERE " + " AND ".join(clauses) +
        " ORDER BY adjudicated_date DESC, adjudication_id DESC LIMIT %s"
    )
    params.append(limit)

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def aggregate_adjudications_by_decision(dept_id: int, date_from: str = None, date_to: str = None) -> list[dict]:
    clauses = ["dept_id = %s"]
    params: list = [dept_id]
    if date_from:
        clauses.append("adjudicated_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("adjudicated_date <= %s")
        params.append(date_to)

    sql = (
        "SELECT decision, COUNT(*) AS record_count, SUM(adjustment_amount) AS total_adjustment "
        "FROM adjudication_records WHERE " + " AND ".join(clauses) +
        " GROUP BY decision ORDER BY record_count DESC"
    )
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def aggregate_adjudications_by_rule(dept_id: int) -> list[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rule_applied, decision, COUNT(*) AS record_count FROM adjudication_records "
                "WHERE dept_id = %s GROUP BY rule_applied, decision ORDER BY rule_applied, record_count DESC",
                (dept_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_latest_adjudication_by_claim(dept_id: int, claim_number: str) -> Optional[dict]:
    """Returns the most recent adjudication record for a claim — since
    claim_number is not unique on this table, "the" adjudication for a
    claim means the latest one, not necessarily the only one."""
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adjudication_records WHERE dept_id = %s AND claim_number = %s "
                "ORDER BY adjudicated_date DESC, adjudication_id DESC LIMIT 1",
                (dept_id, claim_number),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_adjudication_by_id(dept_id: int, adjudication_id: int) -> Optional[dict]:
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM adjudication_records WHERE dept_id = %s AND adjudication_id = %s",
                (dept_id, adjudication_id),
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


_DATE_FIELDS = ["adjudicated_date"]
_DATE_INPUT_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%y"]


def _normalize_date_fields(payload: dict) -> dict:
    normalized = dict(payload)
    for field in _DATE_FIELDS:
        value = normalized.get(field)
        if not value or not isinstance(value, str):
            continue
        parsed = None
        for fmt in _DATE_INPUT_FORMATS:
            try:
                parsed = datetime.strptime(value.strip(), fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise HitlValidationError(
                f"Cannot approve — '{field}' has an unrecognized date value '{value}'. "
                f"Use YYYY-MM-DD (e.g. 2026-12-31)."
            )
        normalized[field] = parsed.isoformat()
    return normalized


def _clean_adjudication_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k in ADJUDICATION_COLUMNS}


def _validate_adjudication_payload(payload: dict) -> None:
    missing = [f for f in REQUIRED_ADJUDICATION_FIELDS if not payload.get(f)]
    if missing:
        raise HitlValidationError(f"Cannot approve — missing required field(s): {', '.join(missing)}")


def approve_hitl_task(task_id: int, reviewer_user_id: Optional[int], edited_payload: Optional[dict] = None,
                       review_notes: Optional[str] = None) -> dict:
    """
    Commits the (possibly reviewer-edited) proposed record. Returns
    {"entity_ref_id": ..., "status": ...}.

    Unlike every other department chatbot in this platform, this ALWAYS
    inserts a new adjudication_records row rather than updating an
    existing one — there is no update-vs-insert branch here at all.
    Two reasons, not one: (1) claim_number is not a unique key on this
    table, so there is no reliable "the existing record for this claim"
    to find, and (2) even if there were, adjudication is realistically
    an append-only event log — a re-adjudication of a claim is a new
    decision event, not a correction overwriting the old one. Mutating
    a past adjudication_records row in place would destroy the audit
    trail of what was actually decided and when.
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

    if task["entity_type"] != "adjudication_records":
        raise HitlValidationError(f"Unsupported entity_type for this chatbot: {task['entity_type']}")

    clean_payload = _clean_adjudication_payload(final_payload)
    clean_payload = _normalize_date_fields(clean_payload)
    _validate_adjudication_payload(clean_payload)

    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cols = ["dept_id"] + list(clean_payload.keys())
            placeholders = ["%s"] * len(cols)
            cur.execute(
                f"INSERT INTO adjudication_records ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
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
