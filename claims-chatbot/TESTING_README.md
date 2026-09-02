# Claims Chatbot — Test Guide

Real PKCE login through Keycloak is confirmed working, and the
entity-key bug fix has been verified live. This guide now uses **real
claim numbers, member IDs, and expected counts pulled directly from
your actual seed data** — no more `CLM1000XX` placeholders to
misread, no more guessing what the "right" answer should be.

## 0. Before you start

Confirm all three services are up:

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

## 1. Data lookup — real claim numbers to use

Every claim number below is real and confirmed present in your data.
Try each and compare the chatbot's table against these exact values:

| Claim number | Member | Status | Notes |
|---|---|---|---|
| `CLM100005` | MBR95477 | IN_REVIEW | billed $13,126.70 |
| `CLM100010` | MBR36772 | DENIED | denial_reason: **Non-covered service** |
| `CLM100028` | MBR21114 | DENIED | denial_reason: **Missing prior authorization** |
| `CLM100060` | MBR37235 | PAID | paid $7,156.25 |
| `CLM100061` | MBR69048 | IN_REVIEW | billed $2,669.17 |
| `CLM100106` | MBR22463 | DENIED | denial_reason: **Duplicate claim** |

```
What is the status of claim CLM100005?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should now say
`"claim_number": "CLM100005"` (the bug fix from last session), and
`retrieved_context` in the `/llm/respond` log should have **exactly 1
item**, not 25.

Try a denial-reason question specifically, since that exercises a
field the earlier "not found" bug never reached:

```
Why was claim CLM100010 denied?
```

**Expect:** the answer should say "Non-covered service" — if it says
anything else or claims it can't find the reason, that's worth
reporting.

### Member lookup — a real caveat worth knowing

```
Show me all claims for member MBR95477
```

**Expect:** in this seed data, **every member currently has exactly
one claim** — no member appears twice in the 110-row `claims` table.
So a correct answer here is a **single-row table**, not multiple rows.
Don't read a one-row result as a bug; it's accurate. If you want to
verify a specific member's row count yourself:
```sql
SELECT member_id, COUNT(*) FROM claims WHERE dept_id = 1 GROUP BY member_id HAVING COUNT(*) > 1;
```
This should currently return **zero rows**.

## 2. Dashboard metric — exact expected counts

```
How many claims are denied vs approved?
```

**Expect:** a chart. The real, tallied counts across all 110 Claims
rows are:

| Status | Count |
|---|---|
| APPROVED | 23 |
| SUBMITTED | 19 |
| IN_REVIEW | 18 |
| PAID | 22 |
| DENIED | 14 |
| APPEALED | 14 |

These numbers come straight from the SQL aggregate the retrieve node
runs — if the chatbot's chart doesn't match this table exactly, the
bug is in retrieval or synthesis, not the LLM's arithmetic (there
isn't any; the LLM only formats a query result you can independently
verify with `SELECT claim_status, COUNT(*) FROM claims WHERE dept_id
= 1 GROUP BY claim_status`).

## 3. Policy question — real knowledge_docs to expect

Claims has 15 real knowledge docs. The core five (each also exists as
a v2/v3 revision):

- **Claims Timely Filing Policy** (POLICY)
- **Duplicate Claim Handling SOP** (CLINICAL_GUIDELINE)
- **COB Determination Guide** (POLICY)
- **Claims Appeals Process** (FAQ)
- **EOB Generation Standards** (REGULATION)

```
What is our timely filing policy for claims?
```

**Expect:** an answer grounded in one of the "Claims Timely Filing
Policy" documents (v1, v2, or v3 — FULLTEXT search picks the best
match). Check the `/llm/respond` log's `retrieved_context` —
`citations` in the response should reference that doc's title.

## 4. Human-in-the-loop — inline approval

Use a **real existing claim number** so the draft has something
concrete to reference:

```
File an appeal for claim CLM100061
```

(`CLM100061` is currently `IN_REVIEW` — appealing an in-review claim
is a realistic scenario, unlike appealing something already `PAID`.)

**Expect:** a "Draft record for your review" card with fields
pre-filled from the real row (member_id `MBR69048`, etc.) and
Approve/Reject buttons.

- Click **Approve & Commit**. Verify it landed:
  ```sql
  SELECT claim_id, claim_status FROM claims WHERE claim_number = 'CLM100061';
  ```
  `claim_status` should now be `APPEALED`, and:
  ```sql
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 1 ORDER BY task_id DESC LIMIT 1;
  ```
  should show `APPROVED` (or `EDITED` if you changed a field) with
  `entity_ref_id` matching the `claim_id` above.

- Repeat with a **new, made-up claim number** (e.g. `CLM999999` —
  confirmed not in the real data) and click **Reject** instead.
  Confirm nothing was written:
  ```sql
  SELECT * FROM claims WHERE claim_number = 'CLM999999';  -- expect 0 rows
  ```

- Try approving with `member_id` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Claims from the
original seed, in a realistic status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 1 GROUP BY status;
```

Click **Review Queue** and confirm the counts you see per status
filter match this query exactly. Then send one more
`File an appeal for claim CLM100060` in chat, but **don't** approve it
inline — go straight to the queue and confirm it shows up there as
`PENDING`, provable from the same table both ways (chat-inserted and
queue-displayed).

## 6. Dashboard

Click **Dashboard**. Your data already has **60 real llm_call_log
rows** and **60 http_call_log rows** for `claims-chatbot`, so the KPI
cards should show non-zero values even before you send a single new
message:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 1 AND chatbot_source = 'claims-chatbot';   -- expect 60 (+ whatever you've added)
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 1 AND chatbot_source = 'claims-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly, and the 14-day chart shows real variation (your seed data
spans dates like `2026-08-14` through `2026-08-27`).

## 7. RBAC — cross-department rejection

Log out, then log back in as a **different department's** test user —
e.g. `billing.tester` / `ChangeMe123!`. **Expect:** a 403 "Your
account is not provisioned for the Claims chatbot." If you get into
the Claims UI instead, stop and report it — this is the one failure
mode not to just note and continue past.

Log back in as `claims.tester` afterward.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5001/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5001` | Browser auto-upgraded to HTTPS | Type `http://localhost:5001/` explicitly, including scheme |
| Terminal stuck at `dquote>` | Unterminated quote (often smart-quote paste corruption) | Ctrl+C repeatedly, or use a heredoc instead of inline quotes |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| Entity extraction looks wrong (`claim_id` instead of `claim_number` in logs) | Old `prompts.py`/`nodes.py` still deployed | Redeploy the fixed files from the previous session |