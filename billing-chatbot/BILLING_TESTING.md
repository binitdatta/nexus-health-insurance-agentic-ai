# Billing Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`billing_records`, `knowledge_docs`, and `hitl_task_queue` rows for
`dept_id = 5` (BILLING) and cross-checked against a live run of the
actual retrieval code — not estimated, not templated.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5005/` as `billing.tester` / `ChangeMe123!`.

## 1. Data lookup — real invoice numbers to use

| Invoice | Member | Status | Amount Due | Amount Paid | Method |
|---|---|---|---|---|---|
| `INV500000` | MBR44734 | OVERDUE | $2,163.47 | $0.00 | — |
| `INV500002` | MBR15848 | PAID | $981.15 | $981.15 | — |
| `INV500025` | MBR99764 | PAID | — | — | ACH |
| `INV500032` | MBR42701 | PARTIAL | — | — | ACH |

```
What is the status of invoice INV500002?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"invoice_number": "INV500002"`, and `retrieved_context` in
`/llm/respond` should have exactly 1 item.

```
Show me overdue invoices
```

**Expect:** a table including `INV500000` (real, OVERDUE) among the
results.

**A real data quirk worth knowing before you test:** `payment_method`
is `NULL` for 65 of the 110 invoices — every invoice that hasn't been
paid yet has no payment method on file, which is realistic (you don't
know how someone will pay until they pay). If you ask about an
`UNPAID` or `OVERDUE` invoice's payment method, a correct answer is
"no payment method on file yet," not an error.

## 2. Dashboard metric — exact expected counts and dollar totals

```
How many invoices are overdue vs paid?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Status | Count |
|---|---|
| OVERDUE | 26 |
| PAID | 26 |
| UNPAID | 21 |
| PARTIAL | 19 |
| WRITTEN_OFF | 18 |

Independently verifiable with:
```sql
SELECT payment_status, COUNT(*) FROM billing_records WHERE dept_id = 5 GROUP BY payment_status;
```

Unlike every other department chatbot so far, Billing's
`dashboard_metric` aggregate also returns real dollar totals, not just
counts — across all 110 rows:

| | Total |
|---|---|
| Total amount due | $151,659.61 |
| Total amount paid | $44,372.10 |

Payment method breakdown (across whichever invoices have one on file):

| Method | Count |
|---|---|
| (none — unpaid) | 65 |
| ACH | 13 |
| CHECK | 13 |
| PAYROLL_DEDUCTION | 10 |
| CREDIT_CARD | 9 |

```
How much do we have outstanding in total?
```

**Expect:** an answer referencing the real $151,659.61 due /
$44,372.10 paid totals (or the outstanding difference between them) —
this is a genuine test of whether the LLM is actually reading the
dollar figures from `retrieved_context` rather than just describing
counts, since this chatbot is the first to have real currency
aggregates to ground on.

## 3. Policy question — real knowledge_docs to expect

Billing has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **Premium Grace Period Policy** — 30 days standard, 90 days for
  APTC-subsidized members
- **Payment Plan Eligibility SOP**
- **Write-Off Approval Thresholds** — tiered by dollar amount
- **Overdue Invoice Escalation SOP** — 30/60/90-day escalation ladder
- **Refund Processing Guidelines**

```
What is our grace period policy for premiums?
```

**Expect:** an answer grounded in "Premium Grace Period Policy" —
should mention the 30-day standard and the 90-day subsidized-member
exception. Verified directly against the live FULLTEXT index: this
query returns "Premium Grace Period Policy" as the #1 relevance match.

```
When can we write off a past-due balance?
```

**Expect:** grounded in "Write-Off Approval Thresholds" — should
mention the ~120-day exhaustion criteria and the dollar-based approval
tiers.

## 4. Human-in-the-loop — inline approval

```
Please set up a payment plan invoice for member MBR55555, $600 due, billing period 2026-09
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new invoice with no existing invoice number, the AI should leave
`invoice_number` in **missing_fields** — fill one in yourself, e.g.
`INVTEST0001`, along with `due_date` if also blank.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT billing_id, payment_status FROM billing_records WHERE invoice_number = 'INVTEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 5 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `billing_id` above.

- Try updating a real, existing invoice instead:
  ```
  Mark invoice INV500032 as paid in full via ACH
  ```
  (`INV500032` is currently `PARTIAL`/ACH — a realistic case to
  complete.) After commit:
  ```sql
  SELECT payment_status FROM billing_records WHERE invoice_number = 'INV500032';
  ```
  This should **update the existing row**, not create a duplicate:
  ```sql
  SELECT COUNT(*) FROM billing_records WHERE invoice_number = 'INV500032';  -- expect 1
  ```

- Try approving with `amount_due` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Billing, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 5 GROUP BY status;
```
| Status | Count |
|---|---|
| PENDING | 6 |
| APPROVED | 2 |
| EDITED | 1 |
| REJECTED | 1 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `billing-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 5 AND chatbot_source = 'billing-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 5 AND chatbot_source = 'billing-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `nursing.tester` / `ChangeMe123!` (a *different*
department). **Expect:** 403 "Your account is not provisioned for the
Billing chatbot." This was independently verified at the token level:
a real signed token minted for `billing-chatbot-pkce` with
`department: BILLING` was confirmed **rejected** by the Gateway when
checked against `dept_code: NURSING`, and confirmed **rejected** by
this chatbot's own `azp` check when validated against
`nursing-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5005/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5005` | Browser auto-upgraded to HTTPS | Type `http://localhost:5005/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_billing_knowledge_docs.sql` against your database |
| Payment method shows blank/null for an unpaid invoice | Not a bug — real data has no method on file until payment happens | Expected for 65 of 110 seeded invoices |
