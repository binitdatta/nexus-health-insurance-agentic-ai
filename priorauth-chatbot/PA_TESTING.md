# Prior Authorization Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`prior_authorizations`, `knowledge_docs`, and `hitl_task_queue` rows
for `dept_id = 2` (PRIORAUTH) and cross-checked against a live run of
the actual retrieval code — not estimated, not templated.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5002/` as `priorauth.tester` / `ChangeMe123!`.

## 1. Data lookup — real PA numbers to use

| PA number | Member | Status | Urgency | Decision reason |
|---|---|---|---|---|
| `PA200000` | MBR66244 | DENIED | ROUTINE | Insufficient clinical documentation |
| `PA200001` | MBR50499 | APPROVED | ROUTINE | Clinical criteria met |
| `PA200010` | MBR59834 | PARTIAL | EMERGENCY | — |
| `PA200014` | MBR67104 | PENDING | URGENT | — |
| `PA200015` | MBR39041 | DENIED | EMERGENCY | — |

```
What is the status of PA200000?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"pa_number": "PA200000"`, and `retrieved_context` in `/llm/respond`
should have exactly 1 item.

```
Why was PA200000 denied?
```

**Expect:** the answer should say "Insufficient clinical
documentation."

## 2. Dashboard metric — exact expected counts

```
How many prior authorizations are pending vs approved?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Status | Count |
|---|---|
| APPROVED | 24 |
| PARTIAL | 23 |
| DENIED | 22 |
| PENDING | 21 |
| EXPIRED | 20 |

Independently verifiable with:
```sql
SELECT status, COUNT(*) FROM prior_authorizations WHERE dept_id = 2 GROUP BY status;
```

The retrieve node also pulls an urgency × status breakdown for this
intent — the real numbers, if you want to probe further:

| Urgency | APPROVED | DENIED | EXPIRED | PARTIAL | PENDING |
|---|---|---|---|---|---|
| EMERGENCY | 4 | 7 | 5 | 7 | 7 |
| ROUTINE | 10 | 9 | 3 | 6 | 5 |
| URGENT | 10 | 6 | 12 | 10 | 9 |

## 3. Policy question — real knowledge_docs to expect

Prior Auth has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **Prior Auth Turnaround SLA** (SOP) — 5 business days routine / 72
  hours urgent / 24 hours emergency
- **Urgent PA Escalation SOP** (FAQ)
- **Medical Necessity Criteria - Imaging** (CLINICAL_GUIDELINE)
- **Peer-to-Peer Review Guidelines** (FAQ)
- **PA Denial Letter Requirements** (CLINICAL_GUIDELINE)

```
What is our turnaround SLA for prior authorization?
```

**Expect:** an answer grounded in "Prior Auth Turnaround SLA" —
should mention the 5 business day / 72 hour / 24 hour tiers. Verified
directly against the live FULLTEXT index: this query returns "Prior
Auth Turnaround SLA" as the #1 relevance match.

## 4. Human-in-the-loop — inline approval

```
Please request prior auth for member MBR12345, provider PRV5000, CPT 99214, routine urgency
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new request with no existing PA number, the AI should leave
`pa_number` in **missing_fields** (it can't invent an identifier) —
fill one in yourself, e.g. `PATEST0001`, along with `requested_date`
and `status` if also left blank.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT pa_id, status FROM prior_authorizations WHERE pa_number = 'PATEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 2 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `pa_id` above.

- Try updating a real, existing PA instead:
  ```
  Approve PA200014 — clinical criteria met
  ```
  (`PA200014` is currently `PENDING`/URGENT — a realistic case to
  approve.) After commit:
  ```sql
  SELECT status, decision_reason FROM prior_authorizations WHERE pa_number = 'PA200014';
  ```
  This should **update the existing row** (same `pa_id` as before),
  not create a duplicate — confirm with:
  ```sql
  SELECT COUNT(*) FROM prior_authorizations WHERE pa_number = 'PA200014';  -- expect 1
  ```

- Try approving with `urgency` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Prior Auth, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 2 GROUP BY status;
```
| Status | Count |
|---|---|
| EDITED | 4 |
| PENDING | 5 |
| REJECTED | 1 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `priorauth-chatbot` from the original seed
(more once you've sent your own test messages):

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 2 AND chatbot_source = 'priorauth-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 2 AND chatbot_source = 'priorauth-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `claims.tester` / `ChangeMe123!` (a *different*
department). **Expect:** 403 "Your account is not provisioned for the
Prior Authorization chatbot." This was independently verified at the
token level, not just the app level — a real signed token minted for
`priorauth-chatbot-pkce` with `department: PRIORAUTH` was confirmed to
be **rejected** by the Gateway when checked against `dept_code:
CLAIMS`, and confirmed to be **rejected** by this chatbot's own
`azp` check when validated against `claims-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5002/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5002` | Browser auto-upgraded to HTTPS | Type `http://localhost:5002/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| HITL draft invents fields not in the form | Old `nodes.py`/Gateway `prompts.py` deployed | This chatbot ships with the `allowed_fields` fix built in from the start — if you see this, confirm you deployed `app/repository.py`'s `PA_COLUMNS` correctly |
