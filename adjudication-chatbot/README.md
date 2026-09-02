# Adjudication Chatbot

The department-facing chatbot for Adjudication — Keycloak PKCE login,
a LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Facility & Providers chatbot with the domain-specific pieces swapped
for `adjudication_records`.

## What's different — and this one has the biggest structural break of any chatbot in the platform so far

| | Facility & Providers | Adjudication |
|---|---|---|
| Domain table | `providers` | `adjudication_records` |
| Identifier column | `provider_code` (**UNIQUE**) | `claim_number` (**NOT unique**) |
| Status values | IN_NETWORK/OUT_OF_NETWORK/PENDING_CREDENTIALING/TERMINATED | APPROVE/DENY/ADJUST/PEND |
| Extra dimension | `specialty` | `rule_applied` (Duplicate Check/Timely Filing/COB Rule/Bundling Edit/Medical Necessity/Fee Schedule Cap) |
| HITL commit behavior | update-in-place or insert | **always insert, never update** |
| Keycloak client | `facprov-chatbot-pkce` | `adjudication-chatbot-pkce` |
| Dev port | 5006 | 5007 |

**Why "always insert" and not the update-vs-insert pattern every other
chatbot uses:** `claim_number` on `adjudication_records` is indexed
but explicitly **not** a unique constraint — a single claim can
legitimately be adjudicated more than once (original decision, then a
correction or re-adjudication after appeal). There is no natural
unique business key on this table at all; the only true identity is
the auto-increment `adjudication_id`. Even if there were a reliable
"find the existing record for this claim" lookup, overwriting it in
place would be wrong: adjudication is realistically an **append-only
event log**. A re-adjudication is a new decision event, not a
correction that should erase what was originally decided.
`approve_hitl_task` in `app/repository.py` reflects this directly —
there is no `SELECT ... WHERE claim_number = ...` existence check at
all, unlike every other chatbot's commit logic. It always inserts.

This is proven, not just asserted: `test_approve_always_inserts_even_when_claim_already_has_a_prior_adjudication`
seeds an existing `DENY` record for a claim, approves a *new* `APPROVE`
HITL task for that same claim, and asserts both rows survive in the
database (2 rows, not 1 overwritten) — while `get_latest_adjudication_by_claim`
correctly surfaces the newer one for retrieval purposes.

**The recurring copy-and-paste identity bug was caught a sixth time**
— `app/extensions.py`'s logger name and `app/security/pkce.py`'s OAuth
salt still said `"facprov_chatbot"` / `"facprov-chatbot-oauth-state"`
immediately after copying. Checked and fixed first, before any domain
logic, per the standing pattern since the Nursing chatbot's README
first flagged this as systematic.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `adjudication_records` + `knowledge_docs`) and only ever
calls the central Gateway for the actual LLM interaction, which is
what gets centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against adjudication columns:
`data_lookup`/`summarize` filter on `claim_number`, `adjudicator_id`,
`decision`, and `rule_applied`, ordered by most recent first;
`dashboard_metric` returns both a decision aggregate (with total
dollar adjustment, not just counts) and a rule × decision breakdown;
`create_record`/`update_record` look up the **latest** adjudication for
a claim (not "the" adjudication — see above) for context and pull
rule-engine/pended-claims policy docs to ground the draft.

## Human-in-the-loop

Unlike the rest of the platform, there is no update-in-place branch —
every approved HITL task inserts a new `adjudication_records` row (see
above for why). Required fields before an approval is accepted:
`claim_number`, `adjudicator_id`, `rule_applied`, `decision`,
`adjudicated_date` (`app/repository.py`'s
`REQUIRED_ADJUDICATION_FIELDS`).

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `ADJUDICATION_COLUMNS` list to the Gateway on every
draft call, so the LLM can't invent field names (e.g. "appeal_status",
"escalation_flag") that have nowhere to be saved. Verified by a
dedicated regression test
(`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `adjudication-chatbot-pkce` against the Central
Realm. Validated by minting a **real signed token** for
`adjudication.tester` via the live token endpoint and running it
through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=ADJUDICATION`, confirmed **rejected** for
   `dept_code=FACPROV`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `facprov-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=7` + `chatbot_source=adjudication-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5007
```

Requires `health_ai_platform` (with `adjudication_records` seed data)
and the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_adjudication_knowledge_docs.sql` once against your database — the
seed data ships with Faker placeholder text in `knowledge_docs`, and
this replaces the 15 Adjudication rows with real, readable policy
content (rule engine ordering, bundling edits, COB adjudication order,
fee schedule caps, pended claims review) so policy-question demos
actually work instead of the LLM correctly refusing to answer from
gibberish.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as the rest of the platform, plus one that
exists nowhere else: `test_pkce.py` (pure crypto), `test_auth_flow.py`,
`test_langgraph_flow.py` (real retrieval against the real seeded
`CLM100000`/adjudication_id 1/ADJ145/Bundling Edit, plus the
`allowed_fields` regression test), `test_hitl.py` — including
`test_approve_always_inserts_even_when_claim_already_has_a_prior_adjudication`,
proving the always-insert design actually preserves both decisions
rather than silently losing history — and `test_dashboard.py` (real
seeded 60-row `llm_call_log`/`http_call_log` counts).

See `ADJUDICATION_TESTING.md` for a full manual test pass built
entirely from real, tallied data — exact decision/rule counts, real
claim numbers, real dollar adjustment totals, real HITL task counts.
