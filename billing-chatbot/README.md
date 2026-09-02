# Billing Chatbot

The department-facing chatbot for Billing — Keycloak PKCE login, a
LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Nursing chatbot with the domain-specific pieces swapped for
`billing_records`.

## What's different from Nursing

| | Nursing | Billing |
|---|---|---|
| Domain table | `nursing_cases` | `billing_records` |
| Identifier column | `case_number` | `invoice_number` |
| Status values | OPEN/IN_PROGRESS/CLOSED | UNPAID/PARTIAL/PAID/OVERDUE/WRITTEN_OFF |
| Extra dimensions | `case_type`, `acuity_level` | `payment_method` (ACH/CHECK/CREDIT_CARD/PAYROLL_DEDUCTION, often NULL for unpaid invoices) |
| Free-text field | `care_plan_notes` | **none** — `billing_records` has no notes column, unlike every other department chatbot so far |
| Keycloak client | `nursing-chatbot-pkce` | `billing-chatbot-pkce` |
| Dev port | 5003 | 5005 |

Everything else — auth flow, Gateway client, HITL commit/validate
pattern, dashboard queries, the UI itself — is unchanged, config-driven
generic code shared across every department chatbot in this platform.

**Two things worth calling out from building this one:**

1. **The recurring copy-and-paste identity bug, caught a fourth time**
   — `app/extensions.py`'s logger name and `app/security/pkce.py`'s
   OAuth state-signing salt still said `"nursing_chatbot"` /
   `"nursing-chatbot-oauth-state"` immediately after copying, exactly
   as flagged as a known risk in the Nursing chatbot's README. Checked
   for and fixed first this time, before touching any domain logic.
2. **No free-text notes field on `billing_records`.** Every other
   department chatbot's test suite marked its own test-inserted rows
   with a `'TEST_ROW_SAFE_TO_DELETE'` sentinel in a notes column for
   safe cleanup. Billing has no such column, so the test cleanup
   convention changed to matching on an `INVTEST%` invoice-number
   prefix instead (`tests/conftest.py`) — noted here rather than
   silently diverging from the established pattern.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `billing_records` + `knowledge_docs`) and only ever calls
the central Gateway for the actual LLM interaction, which is what gets
centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against billing columns:
`data_lookup`/`summarize` filter on `invoice_number`, `member_id`,
`payment_status`, and `billing_period`; `dashboard_metric` returns
both a payment-status aggregate (with billed/paid dollar totals, not
just counts — billing is the first department chatbot where the
dollar amounts themselves are as important as the row counts) and a
payment-method × status breakdown; `create_record`/`update_record`
look up an existing invoice by number for context and pull payment
plan / write-off policy docs to ground the draft.

## Human-in-the-loop

Same commit pattern as the rest of the platform: approving a HITL task
**updates the existing `billing_records` row** if the `invoice_number`
in the (possibly reviewer-edited) payload already exists, or inserts a
new one otherwise — verified with a live test for both branches.

Required fields before an approval is accepted: `invoice_number`,
`member_id`, `billing_period`, `amount_due`, `payment_status`,
`due_date` (`app/repository.py`'s `REQUIRED_BILLING_FIELDS`).

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `BILLING_COLUMNS` list to the Gateway on every draft
call, so the LLM can't invent field names (e.g. "dispute_reason",
"collections_notes") that have nowhere to be saved. Verified by a
dedicated regression test
(`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `billing-chatbot-pkce` against the Central Realm.
Validated by minting a **real signed token** for `billing.tester` via
the live token endpoint and running it through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=BILLING`, confirmed **rejected** for
   `dept_code=NURSING`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `nursing-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=5` + `chatbot_source=billing-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5005
```

Requires `health_ai_platform` (with `billing_records` seed data) and
the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_billing_knowledge_docs.sql` once against your database — the seed
data ships with Faker placeholder text in `knowledge_docs`, and this
replaces the 15 Billing rows with real, readable policy content
(premium grace periods, payment plan eligibility, write-off
thresholds, overdue escalation, refund processing) so policy-question
demos actually work instead of the LLM correctly refusing to answer
from gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as the rest of the platform: `test_pkce.py`
(pure crypto), `test_auth_flow.py`, `test_langgraph_flow.py` (real
retrieval against the real seeded `INV500002`/`MBR15848`, plus the
`allowed_fields` regression test), `test_hitl.py` (approve/reject
against the real `billing_records` table — create vs. update-in-place,
missing required fields, double-approve rejection), `test_dashboard.py`
(real seeded 60-row `llm_call_log`/`http_call_log` counts).

See `BILLING_TESTING.md` for a full manual test pass built entirely
from real, tallied data — exact payment-status/method counts, real
invoice numbers, real dollar totals, real HITL task counts.
