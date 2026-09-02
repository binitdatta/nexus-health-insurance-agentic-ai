# Finance Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`finance_transactions`, `knowledge_docs`, and `hitl_task_queue` rows
for `dept_id = 8` (FINANCE) and cross-checked against a live run of
the actual retrieval code — not estimated, not templated.

**One thing to keep in mind throughout this guide**: `amount` is
**signed**. Inflows (PREMIUM_RECEIPT) are positive; outflows
(CLAIM_PAYOUT, VENDOR_PAYMENT) are negative. A "total" across mixed
transaction types is a net figure, not a sum of absolute values —
that's intentional, not a bug.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5008/` as `finance.tester` / `ChangeMe123!`.

## 1. Data lookup — real transaction references to use

| Reference | Type | Amount | GL Account | Approved By |
|---|---|---|---|---|
| `TXN600000` | VENDOR_PAYMENT | -$206,506.77 | GL-4528 | Jessica Vincent |
| `TXN600001` | ADJUSTMENT | $5,356.73 | GL-4254 | Holly Austin |
| `TXN600010` | PREMIUM_RECEIPT | $338,068.16 | GL-4504 | Joseph Ryan |
| `TXN600012` | PREMIUM_RECEIPT | $424,081.29 | GL-4635 | Justin Collins |

```
What is transaction TXN600000?
```

**Expect:** a table with exactly one row matching the values above —
note the amount should render as **negative** ($-206,506.77). Check
the Gateway's `/llm/intent` log — `entities` should say
`"txn_reference": "TXN600000"`, and `retrieved_context` in
`/llm/respond` should have exactly 1 item.

```
Show me our premium receipts
```

**Expect:** a table where every amount is **positive** — a quick
sanity check that the LLM isn't flattening the sign when describing
the data.

## 2. Dashboard metric — exact expected counts and net totals

```
How much did we pay out in claims vs receive in premiums?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Type | Count | Net Amount |
|---|---|---|
| VENDOR_PAYMENT | 28 | -$6,574,847.27 |
| CLAIM_PAYOUT | 24 | -$6,345,697.02 |
| PREMIUM_RECEIPT | 23 | $5,332,399.16 |
| ACCRUAL | 21 | $4,713,623.32 |
| ADJUSTMENT | 14 | $2,558,711.64 |

Independently verifiable with:
```sql
SELECT txn_type, COUNT(*), ROUND(SUM(amount),2) FROM finance_transactions WHERE dept_id = 8 GROUP BY txn_type;
```

**This is the sharpest test of whether the LLM actually understands
the signed-amount convention**: PREMIUM_RECEIPT ($5.33M) is smaller in
magnitude than either CLAIM_PAYOUT or VENDOR_PAYMENT — a correct
answer should note the plan paid out more than it received in premium
for this period (before accruals/adjustments), not just recite the
numbers without noting the direction.

```
What's our overall net position across all transaction types?
```

**Expect:** an answer in the neighborhood of summing all five net
figures above (~ -$315,810.17) — testing whether the LLM can combine
multiple aggregate rows correctly, not just repeat one.

## 3. Policy question — real knowledge_docs to expect

Finance has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **GL Account Mapping Guide** — txn_type-to-GL-range mapping
- **Month-End Close Checklist** — 5-business-day close window
- **Vendor Payment Approval Policy** — tiered by dollar amount
  ($10k/$50k thresholds)
- **Loss Ratio Calculation Method** — CLAIM_PAYOUT ÷ PREMIUM_RECEIPT
- **Accrual Estimation SOP** — most-recent-comparable-actual method

```
What is our vendor payment approval policy?
```

**Expect:** an answer grounded in "Vendor Payment Approval Policy" —
should mention the analyst/controller/CFO tiers and their dollar
thresholds. Verified directly against the live FULLTEXT index: this
query returns "Vendor Payment Approval Policy" as the #1 relevance
match.

```
How do we calculate loss ratio?
```

**Expect:** grounded in "Loss Ratio Calculation Method" — should
mention CLAIM_PAYOUT divided by PREMIUM_RECEIPT.

## 4. Human-in-the-loop — inline approval

```
Please log a vendor payment of $3,200 to GL-4550
```

**Expect:** a "Draft record for your review" card. Since the LLM
cannot invent a `txn_reference`, that field should land in
**missing_fields** — fill one in yourself, e.g. `TXNTEST0001`. Also
double check the drafted `amount` — a correctly-grounded draft for a
*payment* should propose a **negative** amount, not $3,200 positive;
if it doesn't, that's worth noting as a prompt-grounding issue, not
just approving it as-is.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT txn_id, amount FROM finance_transactions WHERE txn_reference = 'TXNTEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 8 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `txn_id` above.

- Try updating a real, existing transaction instead:
  ```
  Correct the GL account on TXN600001 to GL-4300
  ```
  After commit:
  ```sql
  SELECT gl_account FROM finance_transactions WHERE txn_reference = 'TXN600001';
  ```
  This should **update the existing row**, not create a duplicate:
  ```sql
  SELECT COUNT(*) FROM finance_transactions WHERE txn_reference = 'TXN600001';  -- expect 1
  ```

- Try approving with `gl_account` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Finance, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 8 GROUP BY status;
```
| Status | Count |
|---|---|
| PENDING | 6 |
| APPROVED | 3 |
| REJECTED | 1 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `finance-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 8 AND chatbot_source = 'finance-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 8 AND chatbot_source = 'finance-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `adjudication.tester` / `ChangeMe123!` (a
*different* department). **Expect:** 403 "Your account is not
provisioned for the Finance chatbot." This was independently verified
at the token level: a real signed token minted for
`finance-chatbot-pkce` with `department: FINANCE` was confirmed
**rejected** by the Gateway when checked against
`dept_code: ADJUDICATION`, and confirmed **rejected** by this
chatbot's own `azp` check when validated against
`adjudication-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5008/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5008` | Browser auto-upgraded to HTTPS | Type `http://localhost:5008/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| A drafted vendor payment shows a positive amount | Prompt-grounding issue worth flagging, not a hard error | The LLM should infer sign from txn_type context — note it, don't silently approve |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_finance_knowledge_docs.sql` against your database |
