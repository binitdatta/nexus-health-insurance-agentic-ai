# Central LLM Gateway

The single Flask service every department chatbot calls to talk to
Anthropic. It does the LLM call (intent detection, response
finalization, HITL drafting), enforces that the caller's Keycloak
department claim matches the department it claims to be acting for,
and logs every call to a flat file **and** to `llm_call_log` in MySQL.

Retrieval (SQL against a department's domain tables, RAG lookups
against `knowledge_docs`) is **not** done here — each department
chatbot does its own retrieval (typically a LangGraph node) and hands
the results to this Gateway as `retrieved_context`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness + DB connectivity check |
| POST | `/api/v1/llm/intent` | Classify the user's message into an intent + entities |
| POST | `/api/v1/llm/respond` | Synthesize the final answer from caller-supplied `retrieved_context` |
| POST | `/api/v1/llm/hitl-draft` | Draft a proposed record for human review before it's committed |

All three POST endpoints require:
```
Authorization: Bearer <Keycloak access token>
Content-Type: application/json
```
and a body containing at least `dept_code`, `chatbot_source`, `session_id`, `message`.
See `app/blueprints/llm_gateway.py` for the full per-endpoint schema.

### Example: `/api/v1/llm/respond`

```bash
curl -X POST http://localhost:8000/api/v1/llm/respond \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "dept_code": "CLAIMS",
    "chatbot_source": "claims-chatbot",
    "session_id": "sess-123",
    "message": "What is the status of claim CLM100005?",
    "intent": "data_lookup",
    "retrieved_context": [
      {"source": "claims_row_CLM100005", "type": "sql_row",
       "content": "claim_number=CLM100005, status=IN_REVIEW, billed=1030.59"}
    ]
  }'
```

Response:
```json
{
  "request_id": "...",
  "answer_markdown": "Claim **CLM100005** is currently **IN_REVIEW**.",
  "render": {"type": "table", "spec": {"columns": ["claim_number","status"], "rows": [["CLM100005","IN_REVIEW"]]}},
  "citations": ["claims_row_CLM100005"],
  "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 210, "completion_tokens": 64, "total_cost_usd": 0.001590, "latency_ms": 812}
}
```

## Auth model (Keycloak 26 Central Realm)

Each department chatbot UI is a **PKCE public client** in the Central
Realm. The user authenticates there; the chatbot's backend then relays
that same access token to this Gateway (token relay — there's no
separate service credential to manage, and RBAC stays anchored to the
real department user). The Gateway:

1. Verifies the JWT's signature against Keycloak's JWKS (or a local
   static JWKS file — see `KEYCLOAK_JWKS_STATIC_FILE`), issuer, and
   expiry.
2. Requires the `aud` claim to include `KEYCLOAK_AUDIENCE`
   (`central-llm-api` by default) — add an **Audience mapper** to each
   department's PKCE client for this.
3. Requires a `department` claim (via a **User Attribute -> Token
   Claim** protocol mapper on the realm) that matches the `dept_code`
   in the request body. A Claims-department token cannot be replayed
   against a Billing request — this is enforced in `app/auth.py` and
   covered by `tests/test_gateway.py`.

For local development without a running Keycloak, set
`DEV_BYPASS_AUTH=true` (only honored when `FLASK_ENV=development`) and
pass `X-Debug-Department` / `X-Debug-User` / `X-Debug-Roles` headers
instead of a real token.

## Logging & cost tracking

Every LLM call — success or failure — produces:
- one JSON line in `logs/llm_gateway.log` (rotated at `LOG_MAX_BYTES`)
- one row in `llm_call_log` (MySQL), including full request/response
  payload, token counts, and cost split into input/output/total, keyed
  by `dept_id`, `chatbot_source`, and `session_id` so each chatbot's
  own dashboard can filter to just its department.

Cost is computed from `MODEL_PRICING` in `app/config.py` — it's data,
not code, specifically so pricing changes don't need a deploy.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY, MySQL creds, Keycloak issuer
python wsgi.py          # dev server on :8000
# or, production-style:
gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
```

The MySQL schema must already exist — run `schema.sql` (and optionally
`seed_data.sql`) against your `health_ai_platform` database first; this
app only ever runs hand-written DML against tables it doesn't own.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

`tests/test_gateway.py` exercises all three endpoints against a fake
Anthropic client (no network/API key needed) and, separately, mints a
real RS256-signed JWT against a locally generated JWKS to test the
actual token-validation and department-RBAC logic end to end —
including the negative cases (expired token, wrong issuer, wrong
audience, department mismatch).
