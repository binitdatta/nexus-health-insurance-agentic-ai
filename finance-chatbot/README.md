# Finance Chatbot

The department-facing chatbot for Finance — Keycloak PKCE login, a
LangGraph retrieval/response pipeline, a Bright Blue Bootstrap 5 UI
with table/chart rendering, inline + queue-based human-in-the-loop
record creation, and a cost/call dashboard. Built directly from the
Billing chatbot with the domain-specific pieces swapped for
`finance_transactions`.

## What's different from Billing

| | Billing | Finance |
|---|---|---|
| Domain table | `billing_records` | `finance_transactions` |
| Identifier column | `invoice_number` (UNIQUE) | `txn_reference` (UNIQUE) |
| Status/type values | UNPAID/PARTIAL/PAID/OVERDUE/WRITTEN_OFF | PREMIUM_RECEIPT/CLAIM_PAYOUT/VENDOR_PAYMENT/ACCRUAL/ADJUSTMENT |
| Extra dimension | `payment_method` | `gl_account` |
| **Amount semantics** | `amount_due`/`amount_paid` — always ≥ 0, tracked separately | `amount` — **signed**: positive for inflows, negative for outflows |
| Keycloak client | `billing-chatbot-pkce` | `finance-chatbot-pkce` |
| Dev port | 5005 | 5008 |

**The signed-amount difference is real, not cosmetic.** Billing tracks
`amount_due` and `amount_paid` as two separate non-negative figures.
Finance has a single `amount` column where the sign itself carries
meaning — `PREMIUM_RECEIPT` and (usually) `ACCRUAL`/`ADJUSTMENT` post
positive, `CLAIM_PAYOUT` and `VENDOR_PAYMENT` post negative. This means
`aggregate_finance_by_type`'s `SUM(amount)` is a genuine **net**
figure per transaction type, not a simple total — and it's the first
chatbot in this platform where a naive `SUM()` without understanding
the sign convention would silently produce a misleading number (e.g.
summing all transactions together nets inflows against outflows rather
than showing total volume). `retrieve_node` and the dashboard-metric
prompt handling account for this explicitly.

**The recurring copy-and-paste identity bug was caught a seventh
time** — `app/extensions.py`'s logger name and
`app/security/pkce.py`'s OAuth salt still said `"billing_chatbot"` /
`"billing-chatbot-oauth-state"` immediately after copying. Checked and
fixed first, before any domain logic, per the standing pattern.

## Architecture

Identical to the rest of the platform — see any other department
chatbot's README for the full diagram. This app does its own retrieval
(SQL against `finance_transactions` + `knowledge_docs`) and only ever
calls the central Gateway for the actual LLM interaction, which is
what gets centrally logged to `llm_call_log`.

## The LangGraph pipeline (`app/langgraph_flow/`)

```
classify_intent → retrieve → synthesize ──▶ END
                                        └──▶ hitl_draft ──▶ END   (create_record / update_record)
```

`retrieve_node` branches on intent against finance columns:
`data_lookup`/`summarize` filter on `txn_reference`, `txn_type`,
`gl_account`; `dashboard_metric` returns both a txn_type aggregate
(count + net signed amount) and a GL-account × txn_type breakdown;
`create_record`/`update_record` look up an existing transaction by
reference for context and pull GL-mapping/vendor-approval policy docs
to ground the draft.

## Human-in-the-loop

Same commit pattern as Claims/Prior Auth/Nursing/Billing/Facility &
Providers (unlike Adjudication's always-insert design — see that
chatbot's README for why it's different there): approving a HITL task
**updates the existing `finance_transactions` row** if the
`txn_reference` in the (possibly reviewer-edited) payload already
exists, or inserts a new one otherwise — verified with a live test for
both branches, since `txn_reference` genuinely is a unique key on this
table.

Required fields before an approval is accepted: `txn_reference`,
`txn_type`, `amount`, `txn_date`, `gl_account`
(`app/repository.py`'s `REQUIRED_FINANCE_FIELDS`).

Ships with the schema-passing fix from day one: `hitl_draft_node`
sends the real `FINANCE_COLUMNS` list to the Gateway on every draft
call, so the LLM can't invent field names (e.g. "cost_center",
"budget_line") that have nowhere to be saved. Verified by a dedicated
regression test (`test_hitl_draft_sends_real_schema_not_invented_fields`).

## Auth — validated at the token level, not just the app level

Public PKCE client `finance-chatbot-pkce` against the Central Realm.
Validated by minting a **real signed token** for `finance.tester` via
the live token endpoint and running it through:

1. The Gateway's actual `decode_and_validate()` — confirmed accepted
   for `dept_code=FINANCE`, confirmed **rejected** for
   `dept_code=ADJUDICATION`.
2. This chatbot's actual `verify_token()` — confirmed accepted for its
   own `KEYCLOAK_CLIENT_ID`, confirmed **rejected** when checked
   against `adjudication-chatbot-pkce`'s client ID, proving the `azp`
   client-binding check is actually enforced.

For local dev without a running Keycloak: `DEV_BYPASS_AUTH=true`
exposes `/auth/dev-login`.

## Dashboard

`/dashboard` queries `cost_summary_daily`, `llm_call_log`, and
`http_call_log` filtered to `dept_id=8` + `chatbot_source=finance-chatbot`.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, MySQL creds, Keycloak issuer, Gateway URL
python wsgi.py          # dev server on :5008
```

Requires `health_ai_platform` (with `finance_transactions` seed data)
and the Central LLM Gateway reachable at `GATEWAY_BASE_URL`. Run
`fix_finance_knowledge_docs.sql` once against your database — the seed
data ships with Faker placeholder text in `knowledge_docs`, and this
replaces the 15 Finance rows with real, readable policy content (GL
account mapping, month-end close, vendor payment approval tiers, loss
ratio calculation, accrual estimation) so policy-question demos
actually work instead of the LLM correctly refusing to answer from
gibberish. **A real typo was caught and fixed while building this
file**: one `UPDATE` statement was accidentally written as `UPDATE
knowledge_docs Sample content = ...` instead of `SET content = ...` —
caught by the standard "verify all 15 rows updated" check run against
every knowledge_docs fix in this platform, not a hypothetical risk.

## Tests

```bash
pip install pytest
pytest tests/ -v
```

27 tests, same structure as the rest of the platform: `test_pkce.py`
(pure crypto), `test_auth_flow.py`, `test_langgraph_flow.py` (real
retrieval against the real seeded `TXN600000` / -$206,506.77
VENDOR_PAYMENT, plus the `allowed_fields` regression test),
`test_hitl.py` (approve/reject against the real `finance_transactions`
table — create vs. update-in-place, missing required fields,
double-approve rejection), `test_dashboard.py` (real seeded 60-row
`llm_call_log`/`http_call_log` counts).

See `FINANCE_TESTING.md` for a full manual test pass built entirely
from real, tallied data — exact txn_type counts and net dollar totals,
real transaction references, real HITL task counts.
