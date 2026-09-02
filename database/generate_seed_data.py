#!/usr/bin/env python3
"""
Seed data generator for health_ai_platform.

Produces seed_data.sql with:
  - 10 departments
  - ~15 app_users per department
  - >=100 rows in each department domain table
  - ~15 knowledge_docs per department (policy/SOP/clinical/FAQ mix, for RAG)
  - ~60 sample llm_call_log rows and ~60 http_call_log rows per department
  - ~10 hitl_task_queue rows per department (mixed statuses)
  - cost_summary_daily rollups for the last 14 days per department

Deterministic (Faker seed fixed) so re-running produces identical output,
which is what lets this be checked into source control as a POC fixture.
"""
import json
import random
from datetime import datetime, timedelta, date
from faker import Faker

random.seed(42)
fake = Faker()
Faker.seed(42)

OUT_PATH = "seed_data.sql"
ROWS_PER_DOMAIN_TABLE = 110          # ">= 100 seed data for each department"
DOCS_PER_DEPT = 15
LLM_LOGS_PER_DEPT = 60
HTTP_LOGS_PER_DEPT = 60
HITL_PER_DEPT = 10
USERS_PER_DEPT = 15

# ---------------------------------------------------------------------
# Department catalog. dept_id assigned in insertion order, 1..10.
# ---------------------------------------------------------------------
DEPARTMENTS = [
    ("CLAIMS", "Claims", "Claims intake, review, and payment processing", "claims-chatbot-pkce"),
    ("PRIORAUTH", "Prior Authorization", "Pre-service clinical authorization review", "priorauth-chatbot-pkce"),
    ("NURSING", "Nursing", "Care management and utilization review nursing", "nursing-chatbot-pkce"),
    ("CALLCENTER", "Call Center", "Member and provider inbound call support", "callcenter-chatbot-pkce"),
    ("BILLING", "Billing", "Premium billing and invoicing", "billing-chatbot-pkce"),
    ("FACPROV", "Facility & Providers", "Provider network and facility contracting", "facprov-chatbot-pkce"),
    ("ADJUDICATION", "Adjudication", "Claims adjudication rules and decisioning", "adjudication-chatbot-pkce"),
    ("FINANCE", "Finance", "Corporate finance, GL, and treasury", "finance-chatbot-pkce"),
    ("MANAGEMENT", "Management", "Executive and departmental reporting", "management-chatbot-pkce"),
    ("MEMBERSVC", "Member Services", "Member-facing service tickets and enrollment", "membersvc-chatbot-pkce"),
]

CHATBOT_SOURCE = {
    "CLAIMS": "claims-chatbot",
    "PRIORAUTH": "priorauth-chatbot",
    "NURSING": "nursing-chatbot",
    "CALLCENTER": "callcenter-chatbot",
    "BILLING": "billing-chatbot",
    "FACPROV": "facprov-chatbot",
    "ADJUDICATION": "adjudication-chatbot",
    "FINANCE": "finance-chatbot",
    "MANAGEMENT": "management-chatbot",
    "MEMBERSVC": "membersvc-chatbot",
}

MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
OPERATIONS = ["INTENT_DETECTION", "RESPONSE_FINALIZATION", "RAG_ANSWER", "HITL_DRAFT"]

sql_lines = []


def esc(v):
    """Escape a value for inline SQL literal use."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return "'%s'" % v.isoformat(sep=" ") if isinstance(v, datetime) else "'%s'" % v.isoformat()
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return "'%s'" % s


def insert(table, columns, rows):
    if not rows:
        return
    col_sql = ", ".join(columns)
    sql_lines.append(f"INSERT INTO {table} ({col_sql}) VALUES")
    value_lines = []
    for row in rows:
        vals = ", ".join(esc(v) for v in row)
        value_lines.append(f"  ({vals})")
    sql_lines.append(",\n".join(value_lines) + ";\n")


def rand_date(start_days_ago=365, end_days_ago=0):
    d = random.randint(end_days_ago, start_days_ago)
    return date.today() - timedelta(days=d)


def rand_datetime(start_days_ago=90, end_days_ago=0):
    d = rand_date(start_days_ago, end_days_ago)
    return datetime.combine(d, datetime.min.time()) + timedelta(
        hours=random.randint(7, 19), minutes=random.randint(0, 59), seconds=random.randint(0, 59)
    )


sql_lines.append("USE health_ai_platform;")
sql_lines.append("SET FOREIGN_KEY_CHECKS = 0;\n")
sql_lines.append("-- Clear existing data (idempotent re-seed)")
for t in [
    "cost_summary_daily", "hitl_task_queue", "http_call_log", "llm_call_log", "knowledge_docs",
    "member_services_tickets", "management_reports", "finance_transactions", "adjudication_records",
    "providers", "billing_records", "call_center_logs", "nursing_cases", "prior_authorizations",
    "claims", "app_users", "departments",
]:
    sql_lines.append(f"TRUNCATE TABLE {t};")
sql_lines.append("")

# ---------------------------------------------------------------------
# departments
# ---------------------------------------------------------------------
dept_rows = []
for code, name, desc, client_id in DEPARTMENTS:
    dept_rows.append((code, name, desc, client_id, f"https://internal.example.com/chatbots/{client_id.replace('-pkce','')}", 1))
insert("departments", ["dept_code", "dept_name", "description", "keycloak_client_id", "chatbot_base_url", "is_active"], dept_rows)

dept_id_map = {code: i + 1 for i, (code, *_rest) in enumerate(DEPARTMENTS)}

# ---------------------------------------------------------------------
# app_users
# ---------------------------------------------------------------------
ROLE_BY_DEPT = {
    "CLAIMS": ["claims-analyst", "claims-supervisor"],
    "PRIORAUTH": ["pa-reviewer", "pa-medical-director"],
    "NURSING": ["care-nurse", "nurse-manager"],
    "CALLCENTER": ["call-agent", "call-center-lead"],
    "BILLING": ["billing-specialist", "billing-manager"],
    "FACPROV": ["provider-relations", "network-manager"],
    "ADJUDICATION": ["adjudicator", "adjudication-lead"],
    "FINANCE": ["finance-analyst", "controller"],
    "MANAGEMENT": ["dept-director", "vp"],
    "MEMBERSVC": ["member-svc-rep", "member-svc-lead"],
}

user_rows = []
user_id_counter = 1
user_id_map = {}  # dept_code -> list of user_ids
for code, *_ in DEPARTMENTS:
    user_id_map[code] = []
    for _ in range(USERS_PER_DEPT):
        uname = fake.unique.user_name()
        full_name = fake.name()
        email = f"{uname}@healthplan-example.com"
        sub = fake.unique.uuid4()
        role = random.choice(ROLE_BY_DEPT[code])
        user_rows.append((sub, uname, full_name, email, dept_id_map[code], role, role.replace('-', ' ').title(), 1))
        user_id_map[code].append(user_id_counter)
        user_id_counter += 1
insert(
    "app_users",
    ["keycloak_sub", "username", "full_name", "email", "dept_id", "realm_roles", "job_title", "is_active"],
    user_rows,
)

# ---------------------------------------------------------------------
# Domain tables
# ---------------------------------------------------------------------
CPT_CODES = ["99213", "99214", "99215", "70450", "80053", "93000", "36415", "97110", "J1745", "45378"]
DIAG_CODES = ["E11.9", "I10", "M54.5", "J45.909", "K21.9", "F41.1", "N18.3", "G43.909", "R51", "Z00.00"]
CLAIM_STATUSES = ["SUBMITTED", "IN_REVIEW", "APPROVED", "DENIED", "PAID", "APPEALED"]
DENIAL_REASONS = ["Missing prior authorization", "Non-covered service", "Duplicate claim", "Timely filing exceeded", None, None]

claims_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    status = random.choice(CLAIM_STATUSES)
    billed = round(random.uniform(75, 15000), 2)
    allowed = round(billed * random.uniform(0.4, 0.95), 2)
    paid = round(allowed * random.uniform(0.6, 1.0), 2) if status in ("APPROVED", "PAID") else 0.0
    submitted = rand_date(300, 30)
    processed = submitted + timedelta(days=random.randint(1, 25)) if status != "SUBMITTED" else None
    claims_rows.append((
        f"CLM{100000 + i}", f"MBR{random.randint(10000,99999)}", f"PRV{random.randint(1000,9999)}",
        rand_date(320, 35), random.choice(CPT_CODES), random.choice(DIAG_CODES),
        billed, allowed, paid, status, submitted, processed,
        random.choice(DENIAL_REASONS) if status == "DENIED" else None,
        fake.sentence(nb_words=10),
    ))
insert(
    "claims",
    ["dept_id", "claim_number", "member_id", "provider_id", "date_of_service", "cpt_code", "diagnosis_code",
     "billed_amount", "allowed_amount", "paid_amount", "claim_status", "submitted_date", "processed_date",
     "denial_reason", "notes"],
    [(dept_id_map["CLAIMS"],) + row for row in claims_rows],
)

PA_URGENCY = ["ROUTINE", "URGENT", "EMERGENCY"]
PA_STATUS = ["PENDING", "APPROVED", "DENIED", "PARTIAL", "EXPIRED"]
pa_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    status = random.choice(PA_STATUS)
    requested = rand_date(200, 5)
    decision = requested + timedelta(days=random.randint(1, 14)) if status != "PENDING" else None
    pa_rows.append((
        dept_id_map["PRIORAUTH"], f"PA{200000+i}", f"MBR{random.randint(10000,99999)}", f"PRV{random.randint(1000,9999)}",
        random.choice(CPT_CODES), random.choice(DIAG_CODES), requested, random.choice(PA_URGENCY), status,
        decision, "Clinical criteria met" if status == "APPROVED" else ("Insufficient clinical documentation" if status == "DENIED" else None),
        fake.sentence(nb_words=12),
    ))
insert(
    "prior_authorizations",
    ["dept_id", "pa_number", "member_id", "provider_id", "procedure_code", "diagnosis_code", "requested_date",
     "urgency", "status", "decision_date", "decision_reason", "clinical_notes"],
    pa_rows,
)

CASE_TYPES = ["CARE_MANAGEMENT", "UTILIZATION_REVIEW", "DISEASE_MANAGEMENT", "DISCHARGE_PLANNING"]
ACUITY = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
CASE_STATUS = ["OPEN", "IN_PROGRESS", "CLOSED"]
nursing_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    status = random.choice(CASE_STATUS)
    opened = rand_date(250, 10)
    closed = opened + timedelta(days=random.randint(3, 60)) if status == "CLOSED" else None
    nursing_rows.append((
        dept_id_map["NURSING"], f"NC{300000+i}", f"MBR{random.randint(10000,99999)}", f"RN{random.randint(100,999)}",
        random.choice(CASE_TYPES), random.choice(ACUITY), status, opened, closed, fake.sentence(nb_words=14),
    ))
insert(
    "nursing_cases",
    ["dept_id", "case_number", "member_id", "nurse_id", "case_type", "acuity_level", "status", "opened_date",
     "closed_date", "care_plan_notes"],
    nursing_rows,
)

CALL_TYPES = ["BENEFITS", "CLAIMS_STATUS", "COMPLAINT", "ENROLLMENT", "PROVIDER_SEARCH"]
RESOLUTION = ["RESOLVED", "ESCALATED", "FOLLOW_UP_NEEDED"]
call_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    call_rows.append((
        dept_id_map["CALLCENTER"], f"CALL{400000+i}", f"MBR{random.randint(10000,99999)}", f"AGT{random.randint(100,299)}",
        rand_datetime(180, 0), random.choice(CALL_TYPES), random.randint(45, 1200), random.choice(RESOLUTION),
        fake.sentence(nb_words=10), random.randint(1, 5),
    ))
insert(
    "call_center_logs",
    ["dept_id", "call_reference", "member_id", "agent_id", "call_datetime", "call_type", "duration_seconds",
     "resolution_status", "call_notes", "csat_score"],
    call_rows,
)

PAY_STATUS = ["UNPAID", "PARTIAL", "PAID", "OVERDUE", "WRITTEN_OFF"]
PAY_METHOD = ["ACH", "CREDIT_CARD", "CHECK", "PAYROLL_DEDUCTION"]
billing_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    status = random.choice(PAY_STATUS)
    due = rand_date(200, 1)
    amount_due = round(random.uniform(150, 2400), 2)
    amount_paid = amount_due if status == "PAID" else (round(amount_due * random.uniform(0.2, 0.8), 2) if status == "PARTIAL" else 0.0)
    paid_date = due + timedelta(days=random.randint(-5, 20)) if status in ("PAID", "PARTIAL") else None
    period = due.strftime("%Y-%m")
    billing_rows.append((
        dept_id_map["BILLING"], f"INV{500000+i}", f"MBR{random.randint(10000,99999)}", period, amount_due,
        amount_paid, status, due, paid_date, random.choice(PAY_METHOD) if status in ("PAID", "PARTIAL") else None,
    ))
insert(
    "billing_records",
    ["dept_id", "invoice_number", "member_id", "billing_period", "amount_due", "amount_paid", "payment_status",
     "due_date", "paid_date", "payment_method"],
    billing_rows,
)

SPECIALTIES = ["Family Medicine", "Cardiology", "Orthopedics", "Behavioral Health", "Pediatrics",
               "Radiology", "Physical Therapy", "Gastroenterology", "Dermatology", "OB/GYN"]
NETWORK_STATUS = ["IN_NETWORK", "OUT_OF_NETWORK", "PENDING_CREDENTIALING", "TERMINATED"]
providers_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    start = rand_date(2000, 400)
    providers_rows.append((
        dept_id_map["FACPROV"], f"PRV{1000+i}", fake.company() + " Medical Group", fake.numerify("##########"),
        random.choice(SPECIALTIES), fake.company() + " Facility", random.choice(NETWORK_STATUS),
        fake.address().replace("\n", ", "), fake.phone_number()[:29], start, start + timedelta(days=1095),
    ))
insert(
    "providers",
    ["dept_id", "provider_code", "provider_name", "npi_number", "specialty", "facility_name", "network_status",
     "address", "phone", "contract_start", "contract_end"],
    providers_rows,
)

ADJ_RULES = ["Duplicate Check", "Timely Filing", "Medical Necessity", "COB Rule", "Bundling Edit", "Fee Schedule Cap"]
ADJ_DECISIONS = ["APPROVE", "DENY", "ADJUST", "PEND"]
adjudication_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    decision = random.choice(ADJ_DECISIONS)
    adjudication_rows.append((
        dept_id_map["ADJUDICATION"], f"CLM{100000 + (i % ROWS_PER_DOMAIN_TABLE)}", f"ADJ{random.randint(100,199)}",
        random.choice(ADJ_RULES), decision, round(random.uniform(0, 800), 2) if decision == "ADJUST" else 0.0,
        rand_date(300, 5), fake.sentence(nb_words=10),
    ))
insert(
    "adjudication_records",
    ["dept_id", "claim_number", "adjudicator_id", "rule_applied", "decision", "adjustment_amount",
     "adjudicated_date", "notes"],
    adjudication_rows,
)

TXN_TYPES = ["PREMIUM_RECEIPT", "CLAIM_PAYOUT", "VENDOR_PAYMENT", "ACCRUAL", "ADJUSTMENT"]
finance_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    ttype = random.choice(TXN_TYPES)
    amount = round(random.uniform(500, 500000), 2)
    if ttype in ("CLAIM_PAYOUT", "VENDOR_PAYMENT"):
        amount = -amount
    finance_rows.append((
        dept_id_map["FINANCE"], f"TXN{600000+i}", ttype, amount, "USD", rand_date(365, 1),
        f"GL-{random.randint(4000,4999)}", fake.sentence(nb_words=8), fake.name(),
    ))
insert(
    "finance_transactions",
    ["dept_id", "txn_reference", "txn_type", "amount", "currency", "txn_date", "gl_account", "description",
     "approved_by"],
    finance_rows,
)

REPORT_TITLES = ["Monthly Claims Turnaround", "Prior Auth Denial Trends", "Call Center SLA Summary",
                  "Provider Network Adequacy", "Finance Loss Ratio", "Nursing Case Load Review",
                  "Billing Collections Snapshot", "Adjudication Accuracy Audit", "Member Services CSAT",
                  "Executive KPI Dashboard"]
mgmt_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    covers = random.choice(list(dept_id_map.values()))
    period_month = rand_date(365, 1)
    kpi = json.dumps({
        "volume": random.randint(100, 5000),
        "avg_turnaround_days": round(random.uniform(0.5, 12), 1),
        "sla_pct": round(random.uniform(80, 99.5), 1),
    })
    mgmt_rows.append((
        dept_id_map["MANAGEMENT"], f"RPT{700000+i}", random.choice(REPORT_TITLES) + f" ({period_month.strftime('%b %Y')})",
        covers, period_month.strftime("%Y-%m"), kpi, fake.name(), period_month,
    ))
insert(
    "management_reports",
    ["dept_id", "report_ref", "report_title", "covers_dept_id", "report_period", "kpi_summary", "prepared_by",
     "report_date"],
    mgmt_rows,
)

TICKET_CATEGORY = ["ID_CARD", "ADDRESS_CHANGE", "COVERAGE_QUESTION", "GRIEVANCE", "ENROLLMENT"]
TICKET_PRIORITY = ["LOW", "MEDIUM", "HIGH"]
TICKET_STATUS = ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
tickets_rows = []
for i in range(ROWS_PER_DOMAIN_TABLE):
    status = random.choice(TICKET_STATUS)
    opened = rand_date(200, 1)
    closed = opened + timedelta(days=random.randint(1, 20)) if status in ("RESOLVED", "CLOSED") else None
    tickets_rows.append((
        dept_id_map["MEMBERSVC"], f"TIX{800000+i}", f"MBR{random.randint(10000,99999)}", f"AGT{random.randint(300,399)}",
        random.choice(TICKET_CATEGORY), random.choice(TICKET_PRIORITY), status, opened, closed,
        fake.sentence(nb_words=10) if closed else None,
    ))
insert(
    "member_services_tickets",
    ["dept_id", "ticket_number", "member_id", "agent_id", "category", "priority", "status", "opened_date",
     "closed_date", "resolution_notes"],
    tickets_rows,
)

# ---------------------------------------------------------------------
# knowledge_docs (RAG content, mixed with transactional data at query time)
# ---------------------------------------------------------------------
DOC_TYPES = ["POLICY", "SOP", "CLINICAL_GUIDELINE", "FAQ", "REGULATION"]
DOC_TITLE_TEMPLATES = {
    "CLAIMS": ["Claims Timely Filing Policy", "Duplicate Claim Handling SOP", "COB Determination Guide",
               "Claims Appeals Process", "EOB Generation Standards"],
    "PRIORAUTH": ["Prior Auth Turnaround SLA", "Urgent PA Escalation SOP", "Medical Necessity Criteria - Imaging",
                  "Peer-to-Peer Review Guidelines", "PA Denial Letter Requirements"],
    "NURSING": ["Case Management Acuity Scoring", "Discharge Planning Checklist", "Disease Management Enrollment Criteria",
                "Utilization Review Escalation SOP", "Care Plan Documentation Standard"],
    "CALLCENTER": ["Call Handling Script - Benefits", "Escalation to Grievance Unit SOP", "HIPAA Verbal Verification SOP",
                   "CSAT Survey Administration Policy", "Call Recording Retention Policy"],
    "BILLING": ["Premium Grace Period Policy", "Payment Plan Eligibility SOP", "Write-Off Approval Thresholds",
                "Overdue Invoice Escalation SOP", "Refund Processing Guidelines"],
    "FACPROV": ["Provider Credentialing SOP", "Network Adequacy Standards", "Contract Renewal Checklist",
                "Facility Termination Process", "Fee Schedule Update Policy"],
    "ADJUDICATION": ["Adjudication Rule Engine Overview", "Bundling Edit Reference", "COB Adjudication Order",
                     "Fee Schedule Cap Policy", "Pended Claims Review SOP"],
    "FINANCE": ["GL Account Mapping Guide", "Month-End Close Checklist", "Vendor Payment Approval Policy",
                "Loss Ratio Calculation Method", "Accrual Estimation SOP"],
    "MANAGEMENT": ["KPI Definitions Reference", "Quarterly Business Review Template", "Departmental Reporting Calendar",
                   "Executive Escalation Policy", "Budget Variance Review SOP"],
    "MEMBERSVC": ["ID Card Reissue SOP", "Address Change Verification Policy", "Grievance Intake Procedure",
                  "Open Enrollment FAQ", "Coverage Question Escalation Guide"],
}
docs_rows = []
for code, *_ in DEPARTMENTS:
    titles = DOC_TITLE_TEMPLATES[code]
    for i in range(DOCS_PER_DEPT):
        title = titles[i % len(titles)] + (f" v{1 + i // len(titles)}" if i >= len(titles) else "")
        docs_rows.append((
            dept_id_map[code], title, random.choice(DOC_TYPES),
            fake.paragraph(nb_sentences=6), ",".join(fake.words(nb=3)), "Internal Policy Repository",
        ))
insert("knowledge_docs", ["dept_id", "title", "doc_type", "content", "tags", "source"], docs_rows)

# ---------------------------------------------------------------------
# llm_call_log / http_call_log (representative sample, not exhaustive)
# ---------------------------------------------------------------------
CALL_STATUSES = ["SUCCESS"] * 9 + ["ERROR"]
llm_rows = []
http_rows = []
for code, *_ in DEPARTMENTS:
    d_id = dept_id_map[code]
    source = CHATBOT_SOURCE[code]
    users = user_id_map[code]
    for i in range(LLM_LOGS_PER_DEPT):
        created = rand_datetime(60, 0)
        model = random.choice(MODELS)
        op = random.choice(OPERATIONS)
        prompt_tok = random.randint(200, 3000)
        completion_tok = random.randint(50, 900)
        total_tok = prompt_tok + completion_tok
        in_cost = round(prompt_tok / 1_000_000 * (3.0 if "sonnet" in model else 0.8), 6)
        out_cost = round(completion_tok / 1_000_000 * (15.0 if "sonnet" in model else 4.0), 6)
        status = random.choice(CALL_STATUSES)
        req_id = fake.uuid4()
        llm_rows.append((
            req_id, d_id, random.choice(users), source, fake.uuid4(), model, op,
            "https://api.anthropic.com/v1/messages",
            random.choice(["status_check", "data_lookup", "policy_question", "create_record", "summarize"]),
            json.dumps({"model": model, "max_tokens": 1024, "prompt_preview": fake.sentence(nb_words=8)}),
            json.dumps({"stop_reason": "end_turn", "preview": fake.sentence(nb_words=8)}),
            prompt_tok, completion_tok, total_tok, in_cost, out_cost, round(in_cost + out_cost, 6),
            random.randint(300, 4500), 200 if status == "SUCCESS" else 502, status,
            None if status == "SUCCESS" else "Upstream Anthropic API timeout", created,
        ))
    for i in range(HTTP_LOGS_PER_DEPT):
        created = rand_datetime(60, 0)
        method = random.choice(["POST", "GET"])
        status_code = random.choice([200, 200, 200, 200, 201, 400, 500])
        http_rows.append((
            fake.uuid4(), d_id, random.choice(users), source, fake.uuid4(), method,
            random.choice(["/api/chat", "/api/retrieve", "/api/hitl/propose", "/api/dashboard/summary"]),
            "central-llm-api",
            json.dumps({"query": fake.sentence(nb_words=6)}),
            json.dumps({"ok": status_code < 400}),
            status_code, random.randint(50, 1800), fake.ipv4(), created,
        ))

insert(
    "llm_call_log",
    ["request_id", "dept_id", "user_id", "chatbot_source", "session_id", "model_name", "operation", "endpoint",
     "intent_detected", "request_payload", "response_payload", "prompt_tokens", "completion_tokens", "total_tokens",
     "input_cost_usd", "output_cost_usd", "total_cost_usd", "latency_ms", "http_status", "call_status",
     "error_message", "created_at"],
    llm_rows,
)
insert(
    "http_call_log",
    ["request_id", "dept_id", "user_id", "chatbot_source", "session_id", "http_method", "endpoint", "target_service",
     "request_payload", "response_payload", "response_status", "latency_ms", "client_ip", "created_at"],
    http_rows,
)

# ---------------------------------------------------------------------
# hitl_task_queue (mixed statuses; both inline + queue-review flows)
# ---------------------------------------------------------------------
HITL_ENTITY_BY_DEPT = {
    "CLAIMS": "claims", "PRIORAUTH": "prior_authorizations", "NURSING": "nursing_cases",
    "CALLCENTER": "call_center_logs", "BILLING": "billing_records", "FACPROV": "providers",
    "ADJUDICATION": "adjudication_records", "FINANCE": "finance_transactions",
    "MANAGEMENT": "management_reports", "MEMBERSVC": "member_services_tickets",
}
HITL_STATUSES = ["PENDING", "APPROVED", "REJECTED", "EDITED"]
hitl_rows = []
for code, *_ in DEPARTMENTS:
    d_id = dept_id_map[code]
    source = CHATBOT_SOURCE[code]
    users = user_id_map[code]
    entity = HITL_ENTITY_BY_DEPT[code]
    for i in range(HITL_PER_DEPT):
        status = random.choice(HITL_STATUSES)
        reviewer = random.choice(users) if status != "PENDING" else None
        reviewed_at = rand_datetime(30, 0) if status != "PENDING" else None
        proposed = json.dumps({"member_id": f"MBR{random.randint(10000,99999)}", "note": fake.sentence(nb_words=10)})
        hitl_rows.append((
            d_id, source, fake.uuid4(), random.choice(users), "CREATE", entity, None, proposed, None,
            "AI drafted this record from the conversation; please confirm before committing.",
            status, reviewer,
            fake.sentence(nb_words=8) if status in ("REJECTED", "EDITED") else None, reviewed_at,
        ))
insert(
    "hitl_task_queue",
    ["dept_id", "chatbot_source", "session_id", "requested_by_user_id", "task_type", "entity_type", "entity_ref_id",
     "proposed_payload", "original_payload", "ai_rationale", "status", "reviewer_user_id", "review_notes",
     "reviewed_at"],
    hitl_rows,
)

# ---------------------------------------------------------------------
# cost_summary_daily (last 14 days per department)
# ---------------------------------------------------------------------
summary_rows = []
for code, *_ in DEPARTMENTS:
    d_id = dept_id_map[code]
    source = CHATBOT_SOURCE[code]
    for d in range(14):
        day = date.today() - timedelta(days=d)
        calls = random.randint(5, 80)
        http_calls = calls + random.randint(0, 20)
        p_tok = calls * random.randint(200, 2000)
        c_tok = calls * random.randint(50, 600)
        cost = round((p_tok / 1_000_000 * 3.0) + (c_tok / 1_000_000 * 15.0), 6)
        summary_rows.append((
            d_id, source, day, calls, http_calls, p_tok, c_tok, p_tok + c_tok, cost,
            random.randint(300, 3000), random.randint(50, 1200), random.randint(0, 3),
        ))
insert(
    "cost_summary_daily",
    ["dept_id", "chatbot_source", "summary_date", "total_llm_calls", "total_http_calls", "total_prompt_tokens",
     "total_completion_tokens", "total_tokens", "total_cost_usd", "avg_llm_latency_ms", "avg_http_latency_ms",
     "error_count"],
    summary_rows,
)

sql_lines.append("SET FOREIGN_KEY_CHECKS = 1;")

with open(OUT_PATH, "w") as f:
    f.write("\n".join(sql_lines))

print(f"Wrote {OUT_PATH}")
print(f"Departments: {len(dept_rows)}")
print(f"Users: {len(user_rows)}")
print(f"Claims: {len(claims_rows)}, PA: {len(pa_rows)}, Nursing: {len(nursing_rows)}, Calls: {len(call_rows)}")
print(f"Billing: {len(billing_rows)}, Providers: {len(providers_rows)}, Adjudication: {len(adjudication_rows)}")
print(f"Finance: {len(finance_rows)}, Mgmt Reports: {len(mgmt_rows)}, Tickets: {len(tickets_rows)}")
print(f"Knowledge docs: {len(docs_rows)}")
print(f"LLM logs: {len(llm_rows)}, HTTP logs: {len(http_rows)}, HITL tasks: {len(hitl_rows)}, Summaries: {len(summary_rows)}")
