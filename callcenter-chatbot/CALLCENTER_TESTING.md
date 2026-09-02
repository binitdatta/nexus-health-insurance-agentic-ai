# Call Center Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`call_center_logs`, `knowledge_docs`, and `hitl_task_queue` rows for
`dept_id = 4` (CALLCENTER) and cross-checked against a live run of the
actual retrieval code — not estimated, not templated.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5004/` as `callcenter.tester` / `ChangeMe123!`.

## 1. Data lookup — real call references to use

| Call | Member | Agent | Type | Resolution | CSAT |
|---|---|---|---|---|---|
| `CALL400000` | MBR51180 | AGT145 | ENROLLMENT | ESCALATED | 4 |
| `CALL400001` | MBR46739 | AGT200 | CLAIMS_STATUS | RESOLVED | 5 |
| `CALL400002` | MBR13527 | AGT286 | COMPLAINT | ESCALATED | 4 |
| `CALL400003` | MBR28242 | AGT262 | CLAIMS_STATUS | ESCALATED | 1 |

```
What happened on call CALL400000?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"call_reference": "CALL400000"`, and `retrieved_context` in
`/llm/respond` should have exactly 1 item.

```
Show me escalated claims status calls
```

**Expect:** results should include `CALL400003` (real, CLAIMS_STATUS,
ESCALATED, CSAT 1) — a genuine two-filter test.

## 2. Dashboard metric — exact expected counts and average CSAT

```
What is our average CSAT by call type?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Call Type | Count | Avg CSAT |
|---|---|---|
| COMPLAINT | 28 | 2.93 |
| CLAIMS_STATUS | 24 | 3.21 |
| BENEFITS | 20 | 3.05 |
| PROVIDER_SEARCH | 19 | 3.26 |
| ENROLLMENT | 19 | 2.58 |

Independently verifiable with:
```sql
SELECT call_type, COUNT(*), ROUND(AVG(csat_score),2) FROM call_center_logs WHERE dept_id = 4 GROUP BY call_type;
```

**This is a genuine test of whether the LLM correctly reads an average
rating figure**, not a currency total like every prior chatbot's
dollar aggregates — note that COMPLAINT calls have both the highest
volume (28) and the lowest average satisfaction (2.93), a real pattern
worth the LLM actually surfacing if asked to interpret the data, not
just recite it.

Resolution status breakdown (across all 110 rows):

| Status | Count |
|---|---|
| ESCALATED | 42 |
| FOLLOW_UP_NEEDED | 36 |
| RESOLVED | 32 |

```
How many calls are escalated vs resolved?
```

**Expect:** a chart matching this table.

## 3. Policy question — real knowledge_docs to expect

Call Center has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **Call Handling Script - Benefits** — four-step structure,
  plan-year clarification
- **Escalation to Grievance Unit SOP** — warm-transfer preference
- **HIPAA Verbal Verification SOP** — two-of-four identity check
- **CSAT Survey Administration Policy** — 1-2 score triggers
  supervisor review
- **Call Recording Retention Policy** — 18-month/2-year windows

```
How do we verify a caller's identity?
```

**Expect:** an answer grounded in "HIPAA Verbal Verification SOP" —
should mention needing at least two of: full name, date of birth,
member ID, or address on file. Verified directly against the live
FULLTEXT index: this query returns "HIPAA Verbal Verification SOP" as
the #1 relevance match.

```
When does a low CSAT score trigger a review?
```

**Expect:** grounded in "CSAT Survey Administration Policy" — should
mention a score of 1 or 2 triggering supervisor review.

## 4. Human-in-the-loop — inline approval

```
Please log a benefits call for member MBR55555, resolved
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new call with no existing reference, `call_reference` and
`agent_id` should land in **missing_fields** — fill both in yourself,
e.g. `CALLTEST0001` and `AGTTEST01`, along with a `call_datetime`.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT call_id, resolution_status FROM call_center_logs WHERE call_reference = 'CALLTEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 4 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `call_id` above.

- Try updating a real, existing call instead:
  ```
  Update call CALL400003 — resolved after follow-up, CSAT 3
  ```
  (`CALL400003` is currently ESCALATED with CSAT 1 — a realistic case
  to update after a follow-up.) After commit, confirm it **updated the
  existing row**, not created a duplicate:
  ```sql
  SELECT COUNT(*) FROM call_center_logs WHERE call_reference = 'CALL400003';  -- expect 1
  ```

- Try approving with `call_type` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Call Center, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 4 GROUP BY status;
```
| Status | Count |
|---|---|
| APPROVED | 4 |
| PENDING | 3 |
| REJECTED | 2 |
| EDITED | 1 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `callcenter-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 4 AND chatbot_source = 'callcenter-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 4 AND chatbot_source = 'callcenter-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `management.tester` / `ChangeMe123!` (a
*different* department). **Expect:** 403 "Your account is not
provisioned for the Call Center chatbot." This was independently
verified at the token level: a real signed token minted for
`callcenter-chatbot-pkce` with `department: CALLCENTER` was confirmed
**rejected** by the Gateway when checked against
`dept_code: MANAGEMENT`, and confirmed **rejected** by this chatbot's
own `azp` check when validated against `management-chatbot-pkce`'s
client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5004/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5004` | Browser auto-upgraded to HTTPS | Type `http://localhost:5004/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| A call dated on the last day of a search range is missing | This is the exact bug class this chatbot is built to avoid | Confirm you deployed `repository.py`'s `DATE_ADD(..., INTERVAL 1 DAY)` date_to logic, not a naive `<=` comparison |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_callcenter_knowledge_docs.sql` against your database |
