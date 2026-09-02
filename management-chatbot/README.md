# Management Chatbot

The department-facing chatbot for Management — Keycloak PKCE login, a
LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Adjudication chatbot, but `management_reports` is structurally the
richest table in this platform — two genuinely new patterns needed
real design work, not just a column rename.

## What's new here, not just a domain swap

**1. `covers_dept_id` is a foreign key into `departments` — this is
the first chatbot whose data is *about* other departments.** Every
prior chatbot's table describes its own department's transactions. A
`management_reports` row names which *other* department it covers.
Neither a human typing a chat message nor the LLM extracting entities
can reasonably guess the internal numeric `covers_dept_id` from a
department name — so `repository.list_department_reference()` returns
the real code→id mapping, and `retrieve_node` always includes it in
context for `create_record`/`update_record` intents. On the commit
side, `_normalize_report_payload()` resolves a department **code**
string (`"CLAIMS"`) to the real numeric FK before the row is ever
written, so a draft that names a department instead of guessing an ID
still commits correctly. Verified live: fed the commit path
`covers_dept_id: "CLAIMS"` and confirmed the saved row has
`covers_dept_id = 1` (the CLAIMS row).

**2. `kpi_summary` is JSON text — the first JSON field in this
platform.** It's stored as `LONGTEXT` (schema.sql), not a native JSON
column, but MySQL 8's `JSON_EXTRACT()` works against it directly —
confirmed live before building around it. `aggregate_reports_by_covered_department()`
uses `JSON_EXTRACT(kpi_summary, '$.sla_pct')` to compute a real average
SLA% per covered department, pulled straight out of the JSON text.
On the commit side, the same `_normalize_report_payload()` handles the
mirror problem: if the LLM (or a reviewer) hands back a real nested
dict for `kpi_summary` instead of a pre-serialized string, PyMySQL
cannot bind a dict as a query parameter — it gets `json.dumps()`'d
first. Verified live: fed the commit path a real Python dict and
confirmed the saved column holds valid, parseable JSON text.

**A real data quirk worth knowing before you test**: `report_title`
text and `covers_dept_id` were generated independently at random in
the seed data — a report titled "Call Center SLA Summary" may actually
have `covers_dept_id` pointing at Adjudication, not Call Center. This
is not a chatbot bug; it is a real, verified property of the seed
data (confirmed by joining `management_reports` to `departments`
directly) — see `MANAGEMENT_TESTING.md` for the specific example.

**The recurring copy-and-paste identity bug was caught a ninth time**
— `app/extensions.py`'s logger name and `app/security/pkce.py`'s OAuth
salt still said `"adjudication_chatbot"` / `"adjudication-chatbot-oauth-state"`.
Fixed first, before any domain logic.

**A real bug in my own edit, not the source project, was caught and
fixed mid-build**: the first `repository.py` rewrite left a duplicate
`return cur.fetchone()` line and an orphaned, unused
`get_adjudication_by_id` function behind from an imprecise string
replacement. Caught by re-viewing the file immediately after the edit
rather than assuming it landed cleanly.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `management_reports` + `knowledge_docs`) and only ever
calls the central Gateway for the actual LLM interaction, which is
what gets centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against report columns:
`data_lookup`/`summarize` filter on `report_ref` and a resolved
`covers_dept_id` (accepting either the numeric id or a department
code/name entity); `dashboard_metric` returns the JSON-derived
report-count + average-SLA% breakdown per covered department;
`create_record`/`update_record` look up an existing report by
reference for context and **always** include the department reference
list, since it is otherwise ungroundable.

## Human-in-the-loop

Normal update-or-insert pattern (unlike Adjudication's always-insert
design — `report_ref` genuinely is unique here). Required fields
before an approval is accepted: `report_ref`, `report_title`,
`covers_dept_id`, `report_period`, `report_date`
(`app/repository.py`'s `REQUIRED_REPORT_FIELDS`). An unresolvable
department code in `covers_dept_id` is rejected with a clear error
rather than silently stored as garbage in an integer FK column.

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `REPORT_COLUMNS` list to the Gateway on every draft
call, so the LLM can't invent field names (e.g. "executive_summary",
"action_items") that have nowhere to be saved. Verified by a dedicated
regression test (`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `management-chatbot-pkce` against the Central
Realm. Validated by minting a **real signed token** for
`management.tester` via the live token endpoint and running it
through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=MANAGEMENT`, confirmed **rejected** for
   `dept_code=MEMBERSVC`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `membersvc-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=9` + `chatbot_source=management-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5009
```

Requires `health_ai_platform` (with `management_reports` seed data)
and the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_management_knowledge_docs.sql` once against your database — the
seed data ships with Faker placeholder text in `knowledge_docs`, and
this replaces the 15 Management rows with real, readable policy
content (KPI definitions, quarterly business reviews, the reporting
calendar, executive escalation thresholds, budget variance review) so
policy-question demos actually work instead of the LLM correctly
refusing to answer from gibberish. Every statement in this file was
scanned programmatically for unescaped apostrophes before being run —
a real bug in the Member Services chatbot's equivalent file broke
mid-script from exactly that mistake, and it was worth building the
habit rather than repeating it.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

31 tests (4 more than the platform's usual 27 — dedicated coverage for
the two genuinely new behaviors): `test_pkce.py` (pure crypto),
`test_auth_flow.py`, `test_langgraph_flow.py` (real retrieval against
the real seeded `RPT700000`, department-code resolution, the
department-reference grounding context, plus the `allowed_fields`
regression test), `test_hitl.py` (approve/reject against the real
`management_reports` table, including dedicated tests for department-
code resolution, JSON dict serialization, and rejecting an
unresolvable department code), `test_dashboard.py` (real seeded
60-row `llm_call_log`/`http_call_log` counts).

See `MANAGEMENT_TESTING.md` for a full manual test pass built entirely
from real, tallied data.
