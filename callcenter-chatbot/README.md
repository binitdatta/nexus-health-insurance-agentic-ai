# Call Center Chatbot

The department-facing chatbot for Call Center — Keycloak PKCE login, a
LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Member Services chatbot with the domain-specific pieces swapped for
`call_center_logs`.

**This is the tenth and final department chatbot in this platform.**
Every department from the original architecture request — Claims,
Prior Authorization, Nursing, Call Center, Billing, Facility &
Providers, Adjudication, Finance, Management, Member Services — now
has a working, tested, Keycloak-integrated chatbot talking to the same
central LLM Gateway.

## What's new here — two genuinely new column types, both proven live before being trusted

**1. `call_datetime` is a full DATETIME, not just DATE.** Every prior
chatbot's date filters compared against a `DATE` column, where a
simple `<= date_to` is correct. `call_center_logs` is the first table
with a `DATETIME` column, and a naive `<= date_to` comparison would
silently exclude a call that happened after midnight on the last day
of a range (MySQL treats a bare date string as `00:00:00`).
`search_calls()` and the two aggregate functions use
`call_datetime < DATE_ADD(%s, INTERVAL 1 DAY)` instead. This was not
just reasoned about — it was proven: a real row was inserted at
`2026-05-31 23:59:00`, confirmed it would have been excluded by the
naive comparison, confirmed the actual fix includes it, then the row
was deleted. `tests/test_langgraph_flow.py`'s
`test_data_lookup_datetime_range_includes_full_last_day` keeps that
proof as a permanent regression test.

**2. `csat_score` is the first aggregatable numeric rating column in
this platform.** Every other chatbot's numeric columns were currency
(`amount_due`, `amount`, `adjustment_amount`). `csat_score` is a
nullable 1-5 satisfaction rating.
`aggregate_calls_by_type()`'s `AVG(csat_score)` was verified against
the real seeded data before being trusted — the computed averages
(COMPLAINT 2.93, CLAIMS_STATUS 3.21, BENEFITS 3.05, PROVIDER_SEARCH
3.26, ENROLLMENT 2.58) matched a direct SQL query run independently.

**The recurring copy-and-paste identity bug was caught a tenth time**
— `app/extensions.py`'s logger name and `app/security/pkce.py`'s OAuth
salt still said `"membersvc_chatbot"` /
`"membersvc-chatbot-oauth-state"`. Fixed first, before any domain
logic, exactly as every prior chatbot in this platform required.

**Applying the lesson from Member Services**: before running
`fix_callcenter_knowledge_docs.sql` this time, the whole file was
scanned programmatically for unescaped apostrophes *before* execution,
not after a failure — a real bug in the Member Services chatbot's
equivalent file broke mid-script from exactly that mistake. Clean on
the first run this time.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `call_center_logs` + `knowledge_docs`) and only ever
calls the central Gateway for the actual LLM interaction, which is
what gets centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against call log columns:
`data_lookup`/`summarize` filter on `call_reference`, `member_id`,
`agent_id`, `call_type`, `resolution_status`, and the datetime-aware
date range; `dashboard_metric` returns both a call-type aggregate
(count + average CSAT) and a resolution-status × call-type breakdown;
`create_record`/`update_record` look up an existing call by reference
for context and pull escalation/CSAT/verification policy docs to
ground the draft.

## Human-in-the-loop

Normal update-or-insert pattern — `call_reference` genuinely is
unique. Required fields before an approval is accepted:
`call_reference`, `member_id`, `agent_id`, `call_datetime`,
`call_type`, `resolution_status` (`app/repository.py`'s
`REQUIRED_CALL_FIELDS`).

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `CALL_COLUMNS` list to the Gateway on every draft call,
so the LLM cannot invent field names (e.g. "sentiment_score",
"callback_requested") that have nowhere to be saved. Verified by a
dedicated regression test
(`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `callcenter-chatbot-pkce` against the Central
Realm. Validated by minting a **real signed token** for
`callcenter.tester` via the live token endpoint and running it
through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=CALLCENTER`, confirmed **rejected** for
   `dept_code=MANAGEMENT`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `management-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=4` + `chatbot_source=callcenter-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5004
```

Requires `health_ai_platform` (with `call_center_logs` seed data) and
the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_callcenter_knowledge_docs.sql` once against your database — the
seed data ships with Faker placeholder text in `knowledge_docs`, and
this replaces the 15 Call Center rows with real, readable policy
content (benefits call scripting, grievance escalation, HIPAA verbal
verification, CSAT survey administration, call recording retention)
so policy-question demos actually work instead of the LLM correctly
refusing to answer from gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

28 tests (one more than the platform's usual 27 — a dedicated
regression test for the DATETIME boundary): `test_pkce.py` (pure
crypto), `test_auth_flow.py`, `test_langgraph_flow.py` (real retrieval
against the real seeded `CALL400000`/`MBR51180`, the datetime-boundary
proof, plus the `allowed_fields` regression test), `test_hitl.py`
(approve/reject against the real `call_center_logs` table — create
vs. update-in-place, missing required fields, double-approve
rejection), `test_dashboard.py` (real seeded 60-row
`llm_call_log`/`http_call_log` counts).

See `CALLCENTER_TESTING.md` for a full manual test pass built entirely
from real, tallied data.
