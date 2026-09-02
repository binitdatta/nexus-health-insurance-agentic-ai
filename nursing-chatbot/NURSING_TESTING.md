# Nursing Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`nursing_cases`, `knowledge_docs`, and `hitl_task_queue` rows for
`dept_id = 3` (NURSING) and cross-checked against a live run of the
actual retrieval code — not estimated, not templated.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5003/` as `nursing.tester` / `ChangeMe123!`.

## 1. Data lookup — real case numbers to use

| Case number | Member | Nurse | Case type | Acuity | Status |
|---|---|---|---|---|---|
| `NC300000` | MBR99226 | RN758 | DISEASE_MANAGEMENT | CRITICAL | CLOSED |
| `NC300004` | MBR98686 | RN656 | DISCHARGE_PLANNING | CRITICAL | OPEN |
| `NC300002` | MBR47880 | RN821 | UTILIZATION_REVIEW | CRITICAL | IN_PROGRESS |
| `NC300010` | MBR53292 | — | DISCHARGE_PLANNING | — | OPEN |
| `NC300014` | MBR14888 | — | DISCHARGE_PLANNING | — | CLOSED |

```
What is the status of case NC300000?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"case_number": "NC300000"`, and `retrieved_context` in `/llm/respond`
should have exactly 1 item.

```
Show me open discharge planning cases
```

**Expect:** a table including `NC300004` and `NC300010` (both real,
both OPEN, both DISCHARGE_PLANNING) — should NOT include `NC300014`
(same case type, but CLOSED).

## 2. Dashboard metric — exact expected counts

```
How many nursing cases are open vs closed?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Status | Count |
|---|---|
| OPEN | 46 |
| CLOSED | 39 |
| IN_PROGRESS | 25 |

Independently verifiable with:
```sql
SELECT status, COUNT(*) FROM nursing_cases WHERE dept_id = 3 GROUP BY status;
```

Case type distribution (also all 110 rows):

| Case Type | Count |
|---|---|
| DISCHARGE_PLANNING | 33 |
| DISEASE_MANAGEMENT | 27 |
| UTILIZATION_REVIEW | 26 |
| CARE_MANAGEMENT | 24 |

Acuity level distribution:

| Acuity | Count |
|---|---|
| CRITICAL | 33 |
| MEDIUM | 32 |
| LOW | 26 |
| HIGH | 19 |

```
Break down our caseload by acuity level
```

**Expect:** this should surface the acuity × status breakdown the
retrieve node pulls specifically for `dashboard_metric` intent — ask a
follow-up like "and by status within each acuity" to see the AI use
both aggregates from the same retrieval call if it grounds well.

## 3. Policy question — real knowledge_docs to expect

Nursing has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **Case Management Acuity Scoring** (CLINICAL_GUIDELINE)
- **Discharge Planning Checklist** (varies by seed row)
- **Disease Management Enrollment Criteria**
- **Utilization Review Escalation SOP**
- **Care Plan Documentation Standard**

```
How do we score acuity for case management?
```

**Expect:** an answer grounded in "Case Management Acuity Scoring" —
should mention the LOW/MEDIUM/HIGH/CRITICAL tiers and the
chronic-condition/utilization/medication-complexity scoring factors.
Verified directly against the live FULLTEXT index: this query returns
"Case Management Acuity Scoring" as the #1 relevance match.

```
What's on the discharge planning checklist?
```

**Expect:** grounded in "Discharge Planning Checklist" — should
mention the 24-hour planning start, medication reconciliation, and the
7-day follow-up requirement for high-risk diagnoses.

## 4. Human-in-the-loop — inline approval

```
Please open a discharge planning case for member MBR55555, assign nurse RN700, high acuity
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new case with no existing case number, the AI should leave
`case_number` in **missing_fields** — fill one in yourself, e.g.
`NCTEST0001`, along with `opened_date` and `status` if also blank.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT case_id, status FROM nursing_cases WHERE case_number = 'NCTEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 3 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `case_id` above.

- Try updating a real, existing case instead:
  ```
  Close case NC300002 — utilization review complete
  ```
  (`NC300002` is currently `IN_PROGRESS`/CRITICAL — a realistic case
  to close.) After commit:
  ```sql
  SELECT status FROM nursing_cases WHERE case_number = 'NC300002';
  ```
  This should **update the existing row**, not create a duplicate:
  ```sql
  SELECT COUNT(*) FROM nursing_cases WHERE case_number = 'NC300002';  -- expect 1
  ```

- Try approving with `nurse_id` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Nursing, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 3 GROUP BY status;
```
| Status | Count |
|---|---|
| APPROVED | 4 |
| PENDING | 4 |
| REJECTED | 2 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `nursing-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 3 AND chatbot_source = 'nursing-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 3 AND chatbot_source = 'nursing-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `claims.tester` / `ChangeMe123!` (a *different*
department). **Expect:** 403 "Your account is not provisioned for the
Nursing chatbot." This was independently verified at the token level:
a real signed token minted for `nursing-chatbot-pkce` with
`department: NURSING` was confirmed **rejected** by the Gateway when
checked against `dept_code: BILLING`, and confirmed **rejected** by
this chatbot's own `azp` check when validated against
`priorauth-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5003/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5003` | Browser auto-upgraded to HTTPS | Type `http://localhost:5003/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_nursing_knowledge_docs.sql` against your database |
