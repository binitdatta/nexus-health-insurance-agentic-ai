# Facility & Providers Chatbot — Test Guide

Every number and identifier below was pulled directly from the real
`providers`, `knowledge_docs`, and `hitl_task_queue` rows for
`dept_id = 6` (FACPROV) and cross-checked against a live run of the
actual retrieval code — not estimated, not templated.

**One thing to keep in mind throughout this guide**: unlike Claims,
Prior Auth, Nursing, and Billing, `providers` has **no `member_id`
column**. This is directory data about providers/facilities
themselves — every lookup here is by provider, not by member.

## 0. Before you start

```bash
curl -s http://localhost:8000/api/v1/health   # Gateway — expect "status": "ok"
curl -s http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration | head -3   # Keycloak
```

Log in at `http://localhost:5006/` as `facprov.tester` / `ChangeMe123!`.

## 1. Data lookup — real provider codes to use

| Provider Code | Name | NPI | Specialty | Network Status |
|---|---|---|---|---|
| `PRV1000` | Holloway Ltd Medical Group | 4815698570 | Gastroenterology | PENDING_CREDENTIALING |
| `PRV1001` | Duffy-Wilkerson Medical Group | 3008931734 | Radiology | PENDING_CREDENTIALING |
| `PRV1008` | Wong-Santos Medical Group | 6831472760 | Family Medicine | IN_NETWORK |
| `PRV1009` | Smith, Ray and Tran Medical Group | 4671795760 | Physical Therapy | IN_NETWORK |
| `PRV1015` | Fisher, Zuniga and Torres Medical Group | — | Radiology | TERMINATED |

```
What is the network status of provider PRV1000?
```

**Expect:** a table with exactly one row matching the values above.
Check the Gateway's `/llm/intent` log — `entities` should say
`"provider_code": "PRV1000"`, and `retrieved_context` in
`/llm/respond` should have exactly 1 item.

```
Show me in-network Radiology providers
```

**Expect:** results should NOT include `PRV1001` (Radiology, but
PENDING_CREDENTIALING, not IN_NETWORK) or `PRV1015` (Radiology, but
TERMINATED). This is a genuine multi-filter test — status AND
specialty both need to apply correctly.

```
Search for Holloway
```

**Expect:** a partial-name match returning `PRV1000` — `provider_name`
filtering uses `LIKE`, not an exact match, so a substring search should
work.

## 2. Dashboard metric — exact expected counts

```
How many providers are in-network vs terminated?
```

**Expect:** a chart matching this exact tally of all 110 rows:

| Network Status | Count |
|---|---|
| PENDING_CREDENTIALING | 29 |
| IN_NETWORK | 27 |
| OUT_OF_NETWORK | 27 |
| TERMINATED | 27 |

Independently verifiable with:
```sql
SELECT network_status, COUNT(*) FROM providers WHERE dept_id = 6 GROUP BY network_status;
```

Top specialties by provider count (across all 110 rows):

| Specialty | Count |
|---|---|
| Radiology | 16 |
| Dermatology | 14 |
| OB/GYN | 13 |
| Cardiology | 12 |
| Family Medicine | 11 |

```
Break down our network by specialty and status
```

**Expect:** this should surface the specialty × network-status
breakdown the retrieve node pulls specifically for `dashboard_metric`
intent — a genuinely two-dimensional aggregate, same pattern as Prior
Auth's urgency breakdown and Nursing's acuity breakdown.

## 3. Policy question — real knowledge_docs to expect

Facility & Providers has 15 real knowledge docs (5 core, each with a
v2/v3 revision):

- **Provider Credentialing SOP** — 90-day standard window, primary
  source verification
- **Network Adequacy Standards** — time/distance and wait-time
  thresholds
- **Contract Renewal Checklist** — 120 days before `contract_end`
- **Facility Termination Process** — for-cause vs. without-cause,
  continuity-of-care requirements
- **Fee Schedule Update Policy** — annual, effective January 1

```
What is our provider credentialing process?
```

**Expect:** an answer grounded in "Provider Credentialing SOP" —
should mention the 90-day window and primary source verification
steps (license, NPI, malpractice insurance, OIG/SAM screening).
Verified directly against the live FULLTEXT index: this query returns
"Provider Credentialing SOP" as the #1 relevance match.

```
How do we handle terminating a provider for cause?
```

**Expect:** grounded in "Facility Termination Process" — should
mention due process requirements and continuity-of-care obligations
for members actively in treatment.

## 4. Human-in-the-loop — inline approval

```
Please add New Medical Group as a Family Medicine provider, network status pending
```

**Expect:** a "Draft record for your review" card. Since this is a
brand-new provider with no existing provider code, the AI should leave
`provider_code` and `npi_number` in **missing_fields** — the LLM has
no way to invent a real NPI number, and rightly shouldn't try. Fill
both in yourself, e.g. `PRVTEST0001` and a 10-digit placeholder NPI.

- Click **Approve & Commit**, then verify:
  ```sql
  SELECT provider_id, network_status FROM providers WHERE provider_code = 'PRVTEST0001';
  SELECT status, entity_ref_id FROM hitl_task_queue WHERE dept_id = 6 ORDER BY task_id DESC LIMIT 1;
  ```
  `entity_ref_id` should match the `provider_id` above.

- Try updating a real, existing provider instead:
  ```
  Move provider PRV1000 to in-network status, credentialing complete
  ```
  (`PRV1000` is currently `PENDING_CREDENTIALING` — a realistic case
  to complete.) After commit:
  ```sql
  SELECT network_status FROM providers WHERE provider_code = 'PRV1000';
  ```
  This should **update the existing row**, not create a duplicate:
  ```sql
  SELECT COUNT(*) FROM providers WHERE provider_code = 'PRV1000';  -- expect 1
  ```

- Try approving with `npi_number` cleared out. **Expect:** a red error
  naming the missing field; the task stays `PENDING`.

## 5. Human-in-the-loop — review queue

Your data already has **10 real HITL tasks** for Facility & Providers,
in this exact status mix:

```sql
SELECT status, COUNT(*) FROM hitl_task_queue WHERE dept_id = 6 GROUP BY status;
```
| Status | Count |
|---|---|
| PENDING | 5 |
| APPROVED | 3 |
| REJECTED | 2 |

Click **Review Queue** and confirm the status-filter counts match
this table exactly.

## 6. Dashboard

Your data already has **60 real llm_call_log rows** and **60
http_call_log rows** for `facprov-chatbot` from the original seed:

```sql
SELECT COUNT(*) FROM llm_call_log WHERE dept_id = 6 AND chatbot_source = 'facprov-chatbot';
SELECT ROUND(SUM(total_cost_usd), 4) FROM llm_call_log WHERE dept_id = 6 AND chatbot_source = 'facprov-chatbot';
```

**Expect:** the dashboard's "Total Cost" KPI matches the second query
exactly.

## 7. RBAC — cross-department rejection

Log out, log back in as `billing.tester` / `ChangeMe123!` (a *different*
department). **Expect:** 403 "Your account is not provisioned for the
Facility & Providers chatbot." This was independently verified at the
token level: a real signed token minted for `facprov-chatbot-pkce`
with `department: FACPROV` was confirmed **rejected** by the Gateway
when checked against `dept_code: BILLING`, and confirmed **rejected**
by this chatbot's own `azp` check when validated against
`billing-chatbot-pkce`'s client ID.

## 8. Logout

Click **Log out**. Hitting `http://localhost:5006/` again should
require a fresh login.

## Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_SSL_PROTOCOL_ERROR` on `localhost:5006` | Browser auto-upgraded to HTTPS | Type `http://localhost:5006/` explicitly |
| Chat returns 502 with an Anthropic error | Gateway's `ANTHROPIC_API_KEY` invalid/missing | Check `central-llm-gateway/.env` |
| Chat returns 401 "No access token in session" | Session expired or `DEV_BYPASS_AUTH` mismatch | Log out/in; confirm both `.env` files have `DEV_BYPASS_AUTH=false` |
| 403 on every page after login | Keycloak `department` claim missing/wrong on the user | Check the user's Attributes tab in Keycloak admin console |
| Asking "show claims for provider X" gets a confused answer | This chatbot has no member-linked data — that's a Claims-department question | Expected; providers aren't linked to members in this schema |
| Policy answers say "unable to answer from available information" | `knowledge_docs` still has Faker placeholder text | Run `fix_facprov_knowledge_docs.sql` against your database |
