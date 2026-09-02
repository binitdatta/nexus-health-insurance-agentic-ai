# Management Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`management_reports`, `knowledge_docs`, and `hitl_task_queue` rows for
`dept_id = 9` (MANAGEMENT) and cross-checked against a live run of the
actual retrieval code — not estimated, not templated.

**A real data quirk to know before you test**: `report_title` and
`covers_dept_id` were generated independently at random in the seed
data. `RPT700038` is titled "Call Center SLA Summary" but its real
`covers_dept_id` (verified by joining to `departments`) is
**ADJUDICATION**, not Call Center. If the chatbot's answer about that
report references Adjudication rather than Call Center, that is
**correct** — it means retrieval is reading the real `covers_dept_id`
column rather than guessing from the title text, which is exactly the
right behavior.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5009/` as `management.tester` / `ChangeMe123!`.

## 1. Data lookup — real report references to use

| Report | Title | Covers (real) | Period |
|---|---|---|---|
| `RPT700000` | Call Center SLA Summary (Jan 2026) | FACPROV | 2026-01 |
| `RPT700001` | Member Services CSAT (Aug 2026) | BILLING | 2026-08 |
| `RPT700002` | Adjudication Accuracy Audit (May 2026) | FINANCE | 2026-05 |
| `RPT700038` | Call Center SLA Summary (Apr 2026) | ADJUDICATION | 2026-04 |

```
What does report RPT700000 say?
```

**Expect:** a table with exactly one row matching the values above —
note `covers_dept_id` corresponds to **FACPROV**, not Call Center,
despite the title (see the quirk note above). Check the Gateway's
`/llm/intent` log — `entities` should say `"report_ref": "RPT700000"`.

```
Show me reports covering Claims
```

**Expect:** results filtered by the *real* `covers_dept_id` (resolved
from "Claims" to its numeric id internally) — check the Gateway log's
`retrieved_context` and confirm every row actually has
`covers_dept_id=1`, not just a title containing the word "Claims".

## 2. Dashboard metric — report counts and average SLA by covered department

```
How many reports do we have per department?
```

**Expect:** a chart. Report counts per covered department (real,
independently verifiable):

```sql
SELECT d.dept_code, COUNT(*), ROUND(AVG(CAST(JSON_EXTRACT(mr.kpi_summary,'$.sla_pct') AS DECIMAL(5,2))),2) AS avg_sla
FROM management_reports mr JOIN departments d ON d.dept_id = mr.covers_dept_id
WHERE mr.dept_id = 9 GROUP BY d.dept_code ORDER BY COUNT(*) DESC;
```

| Covers | Count | Avg SLA % |
|---|---|---|
| FACPROV | 15 | 89.77 |
| MANAGEMENT | 15 | 91.73 |
| NURSING | 12 | 90.58 |
| CLAIMS | 11 | 88.15 |
| BILLING | 12 | 90.08 |
| MEMBERSVC | 12 | 89.89 |
| CALLCENTER | 9 | 86.57 |
| ADJUDICATION | 9 | 89.59 |
| FINANCE | 8 | 87.85 |
| PRIORAUTH | 7 | 92.59 |

**This is a genuine test of whether the LLM reads a real average out
of JSON-embedded data**, not a plain numeric column — the `avg_sla`
figure comes from `JSON_EXTRACT()` against `kpi_summary` text, not a
dedicated SLA column.

## 3. Policy question — real knowledge_docs to expect

Management has 15 real knowledge docs (5 core, each with a v2/v3
revision):

- **KPI Definitions Reference** — the three standardized fields
  (volume, avg_turnaround_days, sla_pct)
- **Quarterly Business Review Template** — four-section structure,
  15-business-day deadline
- **Departmental Reporting Calendar** — 10th-business-day monthly
  deadline
- **Executive Escalation Policy** — 10-percentage-point KPI trigger
- **Budget Variance Review SOP** — 5%/15% variance thresholds

```
How are our KPIs defined?
```

**Expect:** an answer grounded in "KPI Definitions Reference" — should
mention volume, avg_turnaround_days, and sla_pct as the three
standardized fields. Verified directly against the live FULLTEXT
index: this query returns "KPI Definitions Reference" as the #1
relevance match.

```
When should we escalate to executives?
```

**Expect:** grounded in "Executive Escalation Policy" — should mention
the 10-percentage-point KPI threshold or regulatory/member-facing risk
triggers.

## 4. Human-in-the-loop — inline approval (the department-code test)

```
Please prepare a new report for Claims covering March 2026
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new report, `report_ref` should land in **missing_fields** — the
LLM cannot invent a real reference number. Check the fields shown:
**`covers_dept_id` should reflect Claims** — either the LLM correctly
resolved "Claims" to `1` already, or you'll see it as a field to
confirm. Fill in a `report_ref` yourself, e.g. `RPTTEST0001`.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT report_id, covers_dept_id FROM management_reports WHERE report_ref = 'RPTTEST0001';
  ```
  `covers_dept_id` should be **1** (Claims), not a literal string.

- Try approving with a nonsense department name instead — edit the
  `covers_dept_id` field to something like `NOTAREALDEPT` before
  clicking Approve. **Expect:** a red error naming the unrecognized
  department code; the task stays `PENDING` rather than silently
  storing garbage in the FK column.

- Try updating a real, existing report instead:
  ```
  Update the title on RPT700000
  ```
  After commit, confirm it **updated the existing row**, not created a
  duplicate:
  ```sql
  SELECT COUNT(*) FROM management_reports WHERE report_ref = 'RPT700000';  -- expect 1
  ```

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Management, in this
exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 9 GROUP BY status;
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
http_call_log rows** for `management-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 9 AND chatbot_source = 'management-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 9 AND chatbot_source = 'management-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `membersvc.tester` / `ChangeMe123!` (a
*different* department). **Expect:** 403 "Your account is not
provisioned for the Management chatbot." This was independently
verified at the token level: a real signed token minted for
`management-chatbot-pkce` with `department: MANAGEMENT` was confirmed
**rejected** by the Gateway when checked against
`dept_code: MEMBERSVC`, and confirmed **rejected** by this chatbot's
own `azp` check when validated against `membersvc-chatbot-pkce`'s
client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5009/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5009` | Browser auto-upgraded to HTTPS | Type `http://localhost:5009/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| A report's covered department does not match its title | Not a bug — real seed data has independently-randomized titles and `covers_dept_id` | See the quirk note at the top of this guide |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_management_knowledge_docs.sql` against your database |
