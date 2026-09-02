# Member Services Chatbot

The department-facing chatbot for Member Services — Keycloak PKCE
login, a LangGraph retrieval/response pipeline, a Bright Blue
Bootstrap 5 UI with table/chart rendering, inline + queue-based
human-in-the-loop record creation, and a cost/call dashboard. Built
directly from the Nursing chatbot with the domain-specific pieces
swapped for `member_services_tickets`.

## What's different from Nursing

| | Nursing | Member Services |
|---|---|---|
| Domain table | `nursing_cases` | `member_services_tickets` |
| Identifier column | `case_number` (UNIQUE) | `ticket_number` (UNIQUE) |
| Status values | OPEN/IN_PROGRESS/CLOSED | OPEN/IN_PROGRESS/RESOLVED/CLOSED |
| Extra dimensions | `case_type`, `acuity_level` | `category` (ID_CARD/ADDRESS_CHANGE/COVERAGE_QUESTION/GRIEVANCE/ENROLLMENT), `priority` (LOW/MEDIUM/HIGH) |
| Keycloak client | `nursing-chatbot-pkce` | `membersvc-chatbot-pkce` |
| Dev port | 5003 | 5010 |

Same normal update-or-insert HITL pattern as Nursing (unlike
Adjudication's always-insert design) — `ticket_number` genuinely is
unique on this table.

**The recurring copy-and-paste identity bug was caught an eighth
time** — `app/extensions.py`'s logger name and
`app/security/pkce.py`'s OAuth salt still said `"nursing_chatbot"` /
`"nursing-chatbot-oauth-state"` immediately after copying. Checked and
fixed first, before any domain logic, per the standing pattern.

**A real SQL bug was caught and fixed while writing the
knowledge_docs content for this chatbot**: one of the 15 `UPDATE`
statements contained the unescaped contraction `"we're looking into
it."` inside a single-quoted SQL string — the apostrophe in `we're`
wasn't doubled (`we''re`), so it closed the string literal early and
broke the SQL syntax. Because `mysql`'s CLI client stops on the first
error by default, this silently killed the **last 3 statements in the
file** (all under "Coverage Question Escalation Guide"), leaving them
as Faker gibberish even though the script reported success up to that
point. Caught by the standard "verify all 15 rows updated" check run
after every knowledge_docs fix in this platform — not a hypothetical
risk, an actual failure that would have shipped silently without that
check. Fixed by rewording to avoid the contraction, verified with a
programmatic scan of the whole file for any other unescaped
apostrophes before re-running (none found), then re-ran and confirmed
all 15 rows via the real `search_knowledge_docs()` call.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `member_services_tickets` + `knowledge_docs`) and only
ever calls the central Gateway for the actual LLM interaction, which
is what gets centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against ticket columns:
`data_lookup`/`summarize` filter on `ticket_number`, `member_id`,
`status`, `category`, and `priority`; `dashboard_metric` returns both
a status aggregate and a category × priority breakdown;
`create_record`/`update_record` look up an existing ticket by number
for context and pull grievance/ID-card/enrollment policy docs to
ground the draft.

## Human-in-the-loop

Approving a HITL task **updates the existing
`member_services_tickets` row** if the `ticket_number` in the
(possibly reviewer-edited) payload already exists, or inserts a new
one otherwise — verified with a live test for both branches.

Required fields before an approval is accepted: `ticket_number`,
`member_id`, `agent_id`, `category`, `priority`, `status`,
`opened_date` (`app/repository.py`'s `REQUIRED_TICKET_FIELDS`).

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `TICKET_COLUMNS` list to the Gateway on every draft
call, so the LLM can't invent field names (e.g. "escalation_level",
"csat_score") that have nowhere to be saved. Verified by a dedicated
regression test (`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `membersvc-chatbot-pkce` against the Central Realm.
Validated by minting a **real signed token** for `membersvc.tester`
via the live token endpoint and running it through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=MEMBERSVC`, confirmed **rejected** for
   `dept_code=FINANCE`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `finance-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=10` + `chatbot_source=membersvc-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5010
```

Requires `health_ai_platform` (with `member_services_tickets` seed
data) and the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_membersvc_knowledge_docs.sql` once against your database — the
seed data ships with Faker placeholder text in `knowledge_docs`, and
this replaces the 15 Member Services rows with real, readable policy
content (ID card reissue, address change verification, grievance
intake, open enrollment, coverage question escalation) so
policy-question demos actually work instead of the LLM correctly
refusing to answer from gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as the rest of the platform: `test_pkce.py`
(pure crypto), `test_auth_flow.py`, `test_langgraph_flow.py` (real
retrieval against the real seeded `TIX800000`/`MBR82088`, plus the
`allowed_fields` regression test), `test_hitl.py` (approve/reject
against the real `member_services_tickets` table — create vs.
update-in-place, missing required fields, double-approve rejection),
`test_dashboard.py` (real seeded 60-row `llm_call_log`/`http_call_log`
counts).

See `MEMBERSVC_TESTING.md` for a full manual test pass built entirely
from real, tallied data — exact status/category/priority counts, real
ticket numbers, real HITL task counts.
