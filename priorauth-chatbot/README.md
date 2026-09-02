# Prior Authorization Chatbot

The department-facing chatbot for Prior Authorization — Keycloak PKCE
login, a LangGraph retrieval/response pipeline, a Bright Blue Bootstrap
5 UI with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Claims chatbot (the platform's reference implementation) with the
domain-specific pieces swapped for `prior_authorizations`.

## What's different from Claims

| | Claims | Prior Authorization |
|---|---|---|
| Domain table | `claims` | `prior_authorizations` |
| Identifier column | `claim_number` | `pa_number` |
| Status values | SUBMITTED/IN_REVIEW/APPROVED/DENIED/PAID/APPEALED | PENDING/APPROVED/DENIED/PARTIAL/EXPIRED |
| Extra dimension | — | `urgency` (ROUTINE/URGENT/EMERGENCY) — its own aggregate query and dashboard-metric breakdown |
| Keycloak client | `claims-chatbot-pkce` | `priorauth-chatbot-pkce` |
| Dev port | 5001 | 5002 |

Everything else — auth flow, Gateway client, HITL commit/validate
pattern, dashboard queries, the UI itself — is unchanged, config-driven
generic code shared across every department chatbot in this platform.

**Two real bugs were caught copying this from Claims**, not cosmetic:
`app/extensions.py` had a hardcoded `"claims_chatbot"` logger name, and
`app/security/pkce.py` had a hardcoded `"claims-chatbot-oauth-state"`
OAuth state-signing salt. Both were leftover identity strings that
would have silently worked (each app has its own `SECRET_KEY`) but
were wrong. Fixed to `"priorauth_chatbot"` / `"priorauth-chatbot-oauth-state"`.

## Architecture

Identical to Claims — see that chatbot's README for the full diagram.
In short: this app does its own retrieval (SQL against
`prior_authorizations` + `knowledge_docs`) and only ever calls the
central Gateway for the actual LLM interaction, which is what gets
centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent exactly like Claims, but against PA
columns: `data_lookup`/`summarize` filter on `pa_number`, `member_id`,
`status`, and `urgency`; `dashboard_metric` returns both a status
aggregate and a urgency × status breakdown (PA volume skews heavily by
urgency tier, so this is genuinely useful, not just decorative);
`create_record`/`update_record` look up an existing PA by number for
context and pull PA-specific policy docs (turnaround SLA, peer-to-peer
review) to ground the draft.

## Human-in-the-loop

Same commit pattern as Claims: approving a HITL task **updates the
existing `prior_authorizations` row** if the `pa_number` in the
(possibly reviewer-edited) payload already exists, or inserts a new
one otherwise — verified with a live test for both branches.

Required fields before an approval is accepted: `pa_number`,
`member_id`, `provider_id`, `procedure_code`, `requested_date`,
`urgency`, `status` (`app/repository.py`'s `REQUIRED_PA_FIELDS`).

This chatbot ships with the schema-passing fix that Claims needed a
follow-up patch for: `hitl_draft_node` sends the real `PA_COLUMNS`
list to the Gateway on every draft call, so the LLM can't invent field
names (e.g. "escalation_reason", "peer_to_peer_notes") that have
nowhere to be saved. Verified by a dedicated regression test
(`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `priorauth-chatbot-pkce` against the Central Realm.
Beyond the standard app-level RBAC check, this chatbot's Keycloak
integration was validated by minting a **real signed token** for
`priorauth.tester` via the live token endpoint and running it through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=PRIORAUTH`, confirmed **rejected** for
   `dept_code=CLAIMS`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against a different client's ID (`claims-chatbot-pkce`), proving
   the `azp` client-binding check — not just the department claim — is
   actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=2` + `chatbot_source=priorauth-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5002
```

Requires `health_ai_platform` (with `prior_authorizations` seed data)
and the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_priorauth_knowledge_docs.sql` once against your database — the
seed data ships with Faker placeholder text in `knowledge_docs`, and
this replaces the 15 Prior Auth rows with real, readable policy
content so policy-question demos actually work instead of the LLM
correctly refusing to answer from gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as Claims: `test_pkce.py` (pure crypto),
`test_auth_flow.py`, `test_langgraph_flow.py` (real retrieval against
the real seeded `PA200000`/`MBR66244`, plus the `allowed_fields`
regression test), `test_hitl.py` (approve/reject against the real
`prior_authorizations` table — create vs. update-in-place, missing
required fields, double-approve rejection), `test_dashboard.py` (real
seeded 60-row `llm_call_log`/`http_call_log` counts).

See `PA_TESTING.md` for a full manual test pass built entirely from
real, tallied data (exact status/urgency counts, real PA numbers, real
HITL task counts) — no placeholders to misread.
