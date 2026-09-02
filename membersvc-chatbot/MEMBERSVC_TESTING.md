# Member Services Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`member_services_tickets`, `knowledge_docs`, and `hitl_task_queue`
rows for `dept_id = 10` (MEMBERSVC) and cross-checked against a live
run of the actual retrieval code — not estimated, not templated.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5010/` as `membersvc.tester` / `ChangeMe123!`.

## 1. Data lookup — real ticket numbers to use

| Ticket | Member | Agent | Category | Priority | Status |
|---|---|---|---|---|---|
| `TIX800000` | MBR82088 | AGT341 | ID_CARD | LOW | RESOLVED |
| `TIX800002` | MBR54294 | AGT386 | GRIEVANCE | MEDIUM | IN_PROGRESS |
| `TIX800003` | MBR27793 | AGT361 | ADDRESS_CHANGE | HIGH | OPEN |
| `TIX800028` | MBR50683 | AGT387 | GRIEVANCE | MEDIUM | CLOSED |

```
What is the status of ticket TIX800000?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"ticket_number": "TIX800000"`, and `retrieved_context` in
`/llm/respond` should have exactly 1 item.

```
Show me open, high priority address change tickets
```

**Expect:** results should include `TIX800003` (real, ADDRESS_CHANGE,
HIGH, OPEN) — a genuine three-filter test (category AND priority AND
status all applied together).

## 2. Dashboard metric — exact expected counts

```
How many tickets are open vs closed?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Status | Count |
|---|---|
| OPEN | 35 |
| CLOSED | 30 |
| RESOLVED | 30 |
| IN_PROGRESS | 15 |

Independently verifiable with:
```sql
SELECT status, COUNT(*) FROM member_services_tickets WHERE dept_id = 10 GROUP BY status;
```

Category breakdown (across all 110 rows):

| Category | Count |
|---|---|
| ADDRESS_CHANGE | 35 |
| ID_CARD | 26 |
| ENROLLMENT | 21 |
| GRIEVANCE | 16 |
| COVERAGE_QUESTION | 12 |

Priority breakdown:

| Priority | Count |
|---|---|
| MEDIUM | 39 |
| HIGH | 37 |
| LOW | 34 |

```
Break down our tickets by category and priority
```

**Expect:** this surfaces the category × priority breakdown the
retrieve node pulls specifically for `dashboard_metric` intent.

## 3. Policy question — real knowledge_docs to expect

Member Services has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **ID Card Reissue SOP** — 7-10 business day standard, 3 free
  replacements/year
- **Address Change Verification Policy** — identity verification
  requirements
- **Grievance Intake Procedure** — 2-business-day acknowledgment,
  30-day resolution
- **Open Enrollment FAQ** — qualifying life events, 30-day reporting
- **Coverage Question Escalation Guide** — when to escalate vs. answer
  directly

```
What is our ID card reissue process?
```

**Expect:** an answer grounded in "ID Card Reissue SOP" — should
mention the 7-10 business day timeline and the free-replacement limit.
Verified directly against the live FULLTEXT index: this query returns
"ID Card Reissue SOP" as the #1 relevance match.

```
When should I escalate a coverage question?
```

**Expect:** grounded in "Coverage Question Escalation Guide" — should
mention plan document ambiguity or active prior authorization/claim
disputes as escalation triggers. **This specific document was the one
broken by a real SQL bug during this chatbot's build** (see README) —
if this query returns Faker gibberish instead of a real answer, the
`fix_membersvc_knowledge_docs.sql` fix didn't take; re-run it.

## 4. Human-in-the-loop — inline approval

```
Please open a grievance ticket for member MBR55555, high priority
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new ticket with no existing ticket number, the AI should leave
`ticket_number` and `agent_id` in **missing_fields** — fill both in
yourself, e.g. `TIXTEST0001` and `AGTTEST01`.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT ticket_id, status FROM member_services_tickets WHERE ticket_number = 'TIXTEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 10 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `ticket_id` above.

- Try updating a real, existing ticket instead:
  ```
  Close ticket TIX800002 — grievance resolved
  ```
  (`TIX800002` is currently `IN_PROGRESS`/GRIEVANCE — a realistic case
  to close.) After commit:
  ```sql
  SELECT status FROM member_services_tickets WHERE ticket_number = 'TIX800002';
  ```
  This should **update the existing row**, not create a duplicate:
  ```sql
  SELECT COUNT(*) FROM member_services_tickets WHERE ticket_number = 'TIX800002';  -- expect 1
  ```

- Try approving with `agent_id` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Member Services, in
this exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 10 GROUP BY status;
```
| Status | Count |
|---|---|
| PENDING | 3 |
| REJECTED | 3 |
| APPROVED | 3 |
| EDITED | 1 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `membersvc-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 10 AND chatbot_source = 'membersvc-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 10 AND chatbot_source = 'membersvc-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `finance.tester` / `ChangeMe123!` (a
*different* department). **Expect:** 403 "Your account is not
provisioned for the Member Services chatbot." This was independently
verified at the token level: a real signed token minted for
`membersvc-chatbot-pkce` with `department: MEMBERSVC` was confirmed
**rejected** by the Gateway when checked against `dept_code: FINANCE`,
and confirmed **rejected** by this chatbot's own `azp` check when
validated against `finance-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5010/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5010` | Browser auto-upgraded to HTTPS | Type `http://localhost:5010/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| Coverage Question Escalation Guide answers are gibberish | The apostrophe-escaping SQL bug wasn't fixed in your copy | Re-run `fix_membersvc_knowledge_docs.sql` — see README for the full story |
