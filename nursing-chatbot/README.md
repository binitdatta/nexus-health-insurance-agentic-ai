# Nursing Chatbot

The department-facing chatbot for Nursing — Keycloak PKCE login, a
LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Prior Authorization chatbot with the domain-specific pieces swapped
for `nursing_cases`.

## What's different from Prior Authorization

| | Prior Authorization | Nursing |
|---|---|---|
| Domain table | `prior_authorizations` | `nursing_cases` |
| Identifier column | `pa_number` | `case_number` |
| Status values | PENDING/APPROVED/DENIED/PARTIAL/EXPIRED | OPEN/IN_PROGRESS/CLOSED |
| Extra dimensions | `urgency` | `case_type` (CARE_MANAGEMENT/UTILIZATION_REVIEW/DISEASE_MANAGEMENT/DISCHARGE_PLANNING) and `acuity_level` (LOW/MEDIUM/HIGH/CRITICAL) — both filterable, with a dedicated acuity × status dashboard-metric breakdown |
| Keycloak client | `priorauth-chatbot-pkce` | `nursing-chatbot-pkce` |
| Dev port | 5002 | 5003 |

Everything else — auth flow, Gateway client, HITL commit/validate
pattern, dashboard queries, the UI itself — is unchanged, config-driven
generic code shared across every department chatbot in this platform.

**A third occurrence of the same copy-and-paste bug was caught here**:
`app/extensions.py`'s logger name and `app/security/pkce.py`'s OAuth
state-signing salt still said `"priorauth_chatbot"` / `"priorauth-chatbot-oauth-state"`
after copying — leftover identity strings from the source project that
would have silently worked (each app has its own `SECRET_KEY`) but
were wrong. This is now a known systematic risk in the
copy-then-adapt workflow for standing up each new department chatbot;
future ones should grep explicitly for the *previous* department's
name before considering the copy complete, not just the domain-logic
files. Fixed to `"nursing_chatbot"` / `"nursing-chatbot-oauth-state"`.

## Architecture

Identical to Claims/Prior Auth — see either of those chatbots' READMEs
for the full diagram. This app does its own retrieval (SQL against
`nursing_cases` + `knowledge_docs`) and only ever calls the central
Gateway for the actual LLM interaction, which is what gets centrally
logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against nursing-case columns:
`data_lookup`/`summarize` filter on `case_number`, `member_id`,
`status`, `case_type`, and `acuity_level`; `dashboard_metric` returns
both a status aggregate and an acuity × status breakdown (case volume
skews heavily by acuity tier, same rationale as Prior Auth's urgency
breakdown); `create_record`/`update_record` look up an existing case
by number for context and pull acuity/discharge-planning policy docs
to ground the draft.

## Human-in-the-loop

Same commit pattern as the rest of the platform: approving a HITL task
**updates the existing `nursing_cases` row** if the `case_number` in
the (possibly reviewer-edited) payload already exists, or inserts a
new one otherwise — verified with a live test for both branches.

Required fields before an approval is accepted: `case_number`,
`member_id`, `nurse_id`, `case_type`, `acuity_level`, `status`,
`opened_date` (`app/repository.py`'s `REQUIRED_NURSING_CASE_FIELDS`).

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `NURSING_CASE_COLUMNS` list to the Gateway on every
draft call, so the LLM can't invent field names (e.g.
"discharge_summary", "readmission_risk_score") that have nowhere to
be saved. Verified by a dedicated regression test
(`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `nursing-chatbot-pkce` against the Central Realm.
Validated by minting a **real signed token** for `nursing.tester` via
the live token endpoint and running it through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=NURSING`, confirmed **rejected** for
   `dept_code=BILLING`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `priorauth-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=3` + `chatbot_source=nursing-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5003
```

Requires `health_ai_platform` (with `nursing_cases` seed data) and the
Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_nursing_knowledge_docs.sql` once against your database — the
seed data ships with Faker placeholder text in `knowledge_docs`, and
this replaces the 15 Nursing rows with real, readable policy content
(acuity scoring, discharge planning, disease management enrollment,
utilization review escalation, care plan documentation standards) so
policy-question demos actually work instead of the LLM correctly
refusing to answer from gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as the rest of the platform: `test_pkce.py`
(pure crypto), `test_auth_flow.py`, `test_langgraph_flow.py` (real
retrieval against the real seeded `NC300000`/`MBR99226`, plus the
`allowed_fields` regression test), `test_hitl.py` (approve/reject
against the real `nursing_cases` table — create vs. update-in-place,
missing required fields, double-approve rejection), `test_dashboard.py`
(real seeded 60-row `llm_call_log`/`http_call_log` counts).

See `NURSING_TESTING.md` for a full manual test pass built entirely
from real, tallied data — exact status/case-type/acuity counts, real
case numbers, real HITL task counts.
