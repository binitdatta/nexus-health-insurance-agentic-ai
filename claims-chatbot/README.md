# Claims Chatbot

The department-facing chatbot for Claims — Keycloak PKCE login, a
LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. This is the reference
implementation the other nine department chatbots (Prior Auth,
Nursing, Call Center, Billing, Facility & Providers, Adjudication,
Finance, Management, Member Services) are copied from — see
"Standing up the next department chatbot" below.

## Architecture

```
User (browser)
   │  PKCE login (public client, Central Realm)
   ▼
Claims Chatbot (this app)
   │  retrieval: SQL against claims + knowledge_docs (this app's own DB access)
   │  every LLM call relayed with the user's own access token ──────┐
   ▼                                                                 ▼
MySQL (health_ai_platform)                          Central LLM Gateway
   ▲  http_call_log (this app's own calls to the Gateway)          │  llm_call_log
   │                                                                 ▼
   └─────────────────────────────────────────────────────  Anthropic API
```

Retrieval never happens in the Gateway — it's this app's job (a
LangGraph node querying `claims` and `knowledge_docs` directly). The
Gateway only ever does the LLM call, and only ever sees what this app
hands it as `retrieved_context`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (only for create_record / update_record)
```

- **classify_intent** — calls the Gateway's `/llm/intent`
- **retrieve** — SQL against `claims` (status/member/claim-number
  lookups, status aggregates) and a `FULLTEXT` search against
  `knowledge_docs` for policy questions — no LLM call
- **synthesize** — calls the Gateway's `/llm/respond` with whatever
  `retrieve` found
- **hitl_draft** — only runs when intent is `create_record` /
  `update_record`; calls the Gateway's `/llm/hitl-draft` and inserts a
  `PENDING` row into `hitl_task_queue`

## Human-in-the-loop

Two ways to review an AI-drafted record, both hitting the same
`app/blueprints/hitl.py` API:

1. **Inline in chat** — the drafted fields appear right under the
   assistant's answer with Approve/Reject buttons.
2. **Review queue** (`/hitl`) — every pending (and past) task across
   all sessions, filterable by status.

Approving **commits to the `claims` table** — inserting a new claim,
or updating one in place if the reviewer-edited `claim_number` already
exists (see `repository.approve_hitl_task`). Required fields
(`claim_number`, `member_id`, `provider_id`, `date_of_service`,
`claim_status`) are validated before anything is written; a task
missing them is left `PENDING` with a clear error rather than silently
failing at the database.

## Auth

Public PKCE client (`claims-chatbot-pkce`) against the Central Realm —
see `app/blueprints/auth_routes.py` and `app/security/pkce.py`. The
OAuth `state` parameter is a signed, self-contained token carrying the
PKCE `code_verifier` (not a server-side session), so the flow doesn't
depend on any cookie surviving the redirect through Keycloak. After
login, this app relays the user's own access token to the Gateway on
every LLM call — there's no separate service credential.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true` (only
honored when `FLASK_ENV=development`) exposes `/auth/dev-login`, which
mints a local session for `DEPT_CODE` directly.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` directly, filtered to `dept_id` + `chatbot_source` for
this chatbot only — KPI cards, a 14-day cost/call-volume chart, and
recent-call tables.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5001
# or, production-style:
gunicorn -w 4 -b 0.0.0.0:5001 wsgi:app
```

Requires the `health_ai_platform` schema + seed data already loaded,
and the Central LLM Gateway running and reachable at `GATEWAY_BASE_URL`.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

25 tests, all against real infrastructure rather than mocks wherever
that's the meaningful thing to test:
- `test_pkce.py` — RFC 7636 code_challenge correctness, signed-state
  roundtrip/tamper/expiry (pure crypto, no DB)
- `test_auth_flow.py` — redirect-when-logged-out, 401 JSON on API
  routes, department-mismatch 403, logout
- `test_langgraph_flow.py` — the full pipeline against the **real
  seeded `claims`/`knowledge_docs` tables**, with only the Gateway's
  three HTTP calls mocked (`canned_gateway_responses` fixture) — this
  is what proves retrieval actually finds the right rows
- `test_hitl.py` — approve/reject against the real `claims` table:
  create vs. update-in-place, reviewer edits, missing-field rejection,
  double-approve rejection, reject leaves `claims` untouched
- `test_dashboard.py` — aggregate queries against the real seeded
  `llm_call_log`/`http_call_log` rows

## Standing up the next department chatbot

This app is intentionally generic wherever department-specific logic
would otherwise leak in. To copy it for, say, Billing:

1. Copy the project, change `DEPT_CODE`, `CHATBOT_SOURCE`,
   `DEPT_DISPLAY_NAME`, `KEYCLOAK_CLIENT_ID` in `.env`
2. Register `billing-chatbot-pkce` in Keycloak (public, PKCE, same
   department-claim and audience mappers as Claims)
3. Replace `app/repository.py`'s claims-specific queries with the new
   department's domain table(s), and `REQUIRED_CLAIM_FIELDS` /
   `CLAIM_COLUMNS` in the HITL section with that table's equivalents
4. Update `app/langgraph_flow/nodes.py`'s `retrieve_node` branches for
   the new domain table
5. Nothing else changes — auth, the Gateway client, the HITL
   commit/validate pattern, and the UI are all department-agnostic
