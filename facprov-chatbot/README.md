# Facility & Providers Chatbot

The department-facing chatbot for Facility & Providers — Keycloak PKCE
login, a LangGraph retrieval/response pipeline, a Bright Blue
Bootstrap 5 UI with table/chart rendering, inline + queue-based
human-in-the-loop record creation, and a cost/call dashboard. Built
directly from the Billing chatbot with the domain-specific pieces
swapped for `providers`.

## What's different from Billing — and from every other department chatbot so far

| | Billing | Facility & Providers |
|---|---|---|
| Domain table | `billing_records` | `providers` |
| Identifier column | `invoice_number` | `provider_code` |
| Status values | UNPAID/PARTIAL/PAID/OVERDUE/WRITTEN_OFF | IN_NETWORK/OUT_OF_NETWORK/PENDING_CREDENTIALING/TERMINATED |
| Extra dimension | `payment_method` | `specialty` — with a dedicated specialty × network-status dashboard-metric breakdown |
| **`member_id` column** | present | **absent** |
| Keycloak client | `billing-chatbot-pkce` | `facprov-chatbot-pkce` |
| Dev port | 5005 | 5006 |

**The `member_id` absence is the real structural difference, not a
minor swap.** Every prior department chatbot (Claims, Prior Auth,
Nursing, Billing) retrieves member-linked transactional data — a
claim, a case, an invoice — always tied back to a member. `providers`
is directory data *about* the providers and facilities themselves; a
provider isn't linked to any one member. `retrieve_node` and
`repository.py` key retrieval on `provider_code`, `npi_number`, and
`provider_name` instead, and there is no member-filtered
`search_providers(member_id=...)` parameter to accidentally rely on.

**Two things worth calling out from building this one:**

1. **The recurring copy-and-paste identity bug, caught a fifth time**
   — checked `app/extensions.py`'s logger name and
   `app/security/pkce.py`'s OAuth salt *immediately* after copying,
   before touching any domain logic, per the pattern established since
   the Nursing chatbot's README first flagged this as systematic.
   Found `"billing_chatbot"` / `"billing-chatbot-oauth-state"` as
   expected; fixed to `"facprov_chatbot"` /
   `"facprov-chatbot-oauth-state"`.
2. **A genuinely new bug, this time in the test itself, not the app**:
   `test_dev_login_then_chat_page_loads` asserted on the literal
   string `"Facility & Providers Assistant"`, but Jinja2 correctly
   HTML-escapes `&` to `&amp;` in `{{ dept_display_name }}` for XSS
   safety. The app was right; the test was wrong. Fixed the assertion
   to expect the escaped string — same category of mistake as an
   earlier Flask-session-mutation test bug in the Claims chatbot: a
   failing assertion isn't automatically an app bug.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `providers` + `knowledge_docs`) and only ever calls the
central Gateway for the actual LLM interaction, which is what gets
centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against provider-directory columns:
`data_lookup`/`summarize` filter on `provider_code`, `npi_number`,
`provider_name` (partial match via `LIKE`), `specialty`, and
`network_status`; `dashboard_metric` returns both a network-status
aggregate and a specialty × network-status breakdown; `create_record`/
`update_record` look up an existing provider by code for context and
pull credentialing/termination policy docs to ground the draft.

## Human-in-the-loop

Same commit pattern as the rest of the platform: approving a HITL task
**updates the existing `providers` row** if the `provider_code` in the
(possibly reviewer-edited) payload already exists, or inserts a new
one otherwise — verified with a live test for both branches.

Required fields before an approval is accepted: `provider_code`,
`provider_name`, `npi_number`, `network_status`
(`app/repository.py`'s `REQUIRED_PROVIDER_FIELDS`). `specialty`,
`facility_name`, `address`, `phone`, and contract dates are optional,
matching the nullable columns in `schema.sql`.

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `PROVIDER_COLUMNS` list to the Gateway on every draft
call, so the LLM can't invent field names (e.g. "termination_reason",
"quality_score") that have nowhere to be saved. Verified by a
dedicated regression test
(`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `facprov-chatbot-pkce` against the Central Realm.
Validated by minting a **real signed token** for `facprov.tester` via
the live token endpoint and running it through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=FACPROV`, confirmed **rejected** for
   `dept_code=BILLING`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `billing-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=6` + `chatbot_source=facprov-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5006
```

Requires `health_ai_platform` (with `providers` seed data) and the
Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_facprov_knowledge_docs.sql` once against your database — the seed
data ships with Faker placeholder text in `knowledge_docs`, and this
replaces the 15 Facility & Providers rows with real, readable policy
content (credentialing, network adequacy, contract renewal, facility
termination, fee schedule updates) so policy-question demos actually
work instead of the LLM correctly refusing to answer from gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as the rest of the platform: `test_pkce.py`
(pure crypto), `test_auth_flow.py`, `test_langgraph_flow.py` (real
retrieval against the real seeded `PRV1000`/Holloway Ltd Medical
Group, plus the `allowed_fields` regression test), `test_hitl.py`
(approve/reject against the real `providers` table — create vs.
update-in-place, missing required fields, double-approve rejection),
`test_dashboard.py` (real seeded 60-row `llm_call_log`/`http_call_log`
counts).

See `FACPROV_TESTING.md` for a full manual test pass built entirely
from real, tallied data — exact network-status/specialty counts, real
provider codes, real HITL task counts.
