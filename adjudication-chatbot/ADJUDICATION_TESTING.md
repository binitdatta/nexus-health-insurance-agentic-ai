# Adjudication Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`adjudication_records`, `knowledge_docs`, and `hitl_task_queue` rows
for `dept_id = 7` (ADJUDICATION) and cross-checked against a live run
of the actual retrieval code — not estimated, not templated.

**One thing to keep in mind throughout this guide**: `claim_number` on
this table is **not unique** — a claim can have more than one
adjudication record over time. Lookups here surface the *latest*
adjudication for a claim, not necessarily its only one.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5007/` as `adjudication.tester` / `ChangeMe123!`.

## 1. Data lookup — real claim numbers to use

| Claim | Adjudicator | Rule | Decision | Adjustment |
|---|---|---|---|---|
| `CLM100000` | ADJ145 | Bundling Edit | APPROVE | $0.00 |
| `CLM100001` | ADJ187 | Medical Necessity | ADJUST | $485.25 |
| `CLM100002` | ADJ148 | COB Rule | PEND | $0.00 |
| `CLM100005` | ADJ181 | Timely Filing | ADJUST | $238.45 |
| `CLM100009` | ADJ126 | Fee Schedule Cap | ADJUST | $593.14 |

```
How was claim CLM100000 adjudicated?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"claim_number": "CLM100000"`, and `retrieved_context` in
`/llm/respond` should have exactly 1 item.

```
Why was claim CLM100005 adjusted?
```

**Expect:** the answer should reference the Timely Filing rule and the
$238.45 adjustment amount.

## 2. Dashboard metric — exact expected counts and dollar totals

```
How many claims were approved vs denied?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Decision | Count |
|---|---|
| ADJUST | 32 |
| APPROVE | 30 |
| PEND | 27 |
| DENY | 21 |

Independently verifiable with:
```sql
SELECT decision, COUNT(*) FROM adjudication_records WHERE dept_id = 7 GROUP BY decision;
```

Total adjustment dollars across all 110 rows: **$13,182.74**. Ask:

```
What is our total adjustment amount?
```

**Expect:** an answer referencing this figure or close to it depending
on date filters — this genuinely tests whether the LLM is reading the
dollar aggregate from `retrieved_context`, not just describing counts
(same class of test as Billing's dollar-total question).

Rule breakdown (across all 110 rows):

| Rule | Count |
|---|---|
| Timely Filing | 28 |
| Bundling Edit | 21 |
| Fee Schedule Cap | 21 |
| COB Rule | 17 |
| Medical Necessity | 15 |
| Duplicate Check | 8 |

```
Break down our adjudication decisions by rule
```

**Expect:** this surfaces the rule × decision breakdown the retrieve
node pulls specifically for `dashboard_metric` intent.

## 3. Policy question — real knowledge_docs to expect

Adjudication has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **Adjudication Rule Engine Overview** — the fixed rule evaluation
  order (Duplicate Check → Timely Filing → COB Rule → Bundling Edit →
  Medical Necessity → Fee Schedule Cap)
- **Bundling Edit Reference** — NCCI-sourced, modifier 59 handling
- **COB Adjudication Order** — secondary-payer calculation
- **Fee Schedule Cap Policy** — contracted-rate capping via ADJUST
- **Pended Claims Review SOP** — 5-business-day review requirement

```
How does our bundling edit rule work?
```

**Expect:** an answer grounded in "Bundling Edit Reference" — should
mention NCCI edits and that bundling produces an ADJUST, not a hard
DENY. Verified directly against the live FULLTEXT index: this query
returns "Bundling Edit Reference" as the #1 relevance match.

```
What order do adjudication rules run in?
```

**Expect:** grounded in "Adjudication Rule Engine Overview" — should
list the six rules roughly in the documented order.

## 4. Human-in-the-loop — inline approval (behaves differently here)

```
Adjudicate claim CLM100050 under the duplicate check rule, deny it
```

**Expect:** a "Draft record for your review" card. Since the LLM
cannot invent an `adjudicator_id`, that field should land in
**missing_fields** — fill one in yourself, e.g. `ADJTEST01`.

- Click **Approve & Commit**, then verify a **new row was inserted**:
  ```sql
  SELECT adjudication_id, decision FROM adjudication_records WHERE claim_number = 'CLM100050' ORDER BY adjudication_id;
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 7 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the new `adjudication_id`.

- **The behavior that's different from every other chatbot**: now
  submit a *second* adjudication for the *same* claim:
  ```
  Re-adjudicate claim CLM100050 under medical necessity, approve it this time
  ```
  Approve that too, then check:
  ```sql
  SELECT adjudication_id, rule_applied, decision FROM adjudication_records WHERE claim_number = 'CLM100050' ORDER BY adjudication_id;
  ```
  **Expect: two rows**, not one updated row — the original DENY and
  the new APPROVE should both still exist. This is the core behavior
  this chatbot is built to prove; if you only see one row, something
  regressed.

- Try approving with `rule_applied` cleared out. **Expect:** a red
  error naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Adjudication, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 7 GROUP BY status;
```
| Status | Count |
|---|---|
| PENDING | 3 |
| REJECTED | 3 |
| APPROVED | 2 |
| EDITED | 2 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `adjudication-chatbot` from the original
seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 7 AND chatbot_source = 'adjudication-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 7 AND chatbot_source = 'adjudication-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `facprov.tester` / `ChangeMe123!` (a *different*
department). **Expect:** 403 "Your account is not provisioned for the
Adjudication chatbot." This was independently verified at the token
level: a real signed token minted for `adjudication-chatbot-pkce` with
`department: ADJUDICATION` was confirmed **rejected** by the Gateway
when checked against `dept_code: FACPROV`, and confirmed **rejected**
by this chatbot's own `azp` check when validated against
`facprov-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5007/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5007` | Browser auto-upgraded to HTTPS | Type `http://localhost:5007/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| Approving a second adjudication for the same claim seems to "lose" the first | It shouldn't — this is the one thing this chatbot is built to get right | Re-check with `SELECT * FROM adjudication_records WHERE claim_number = '...'`; if only 1 row exists, that's a real regression worth reporting |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_adjudication_knowledge_docs.sql` against your database |
