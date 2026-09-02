# Central Realm — Keycloak 26 export

`realm-export.json` for `health-ai-central`, wired to match exactly
what `central-llm-gateway` and every department chatbot already expect
out of the box. **Actually validated**, not just hand-written: imported
into a real Keycloak 26.0.7 instance, logged in as a real test user,
and ran the resulting signed token through both apps' real (not
test-double) token-validation code — see "How this was validated"
below.

## What's in it

- **10 public PKCE clients**, one per department: `claims-chatbot-pkce`,
  `priorauth-chatbot-pkce`, `nursing-chatbot-pkce`,
  `callcenter-chatbot-pkce`, `billing-chatbot-pkce`,
  `facprov-chatbot-pkce`, `adjudication-chatbot-pkce`,
  `finance-chatbot-pkce`, `management-chatbot-pkce`,
  `membersvc-chatbot-pkce`. Each: public, `S256` PKCE required, standard
  flow only (no direct grants, no implicit flow), redirect URI
  `http://localhost:<port>/auth/callback` where port is 5001 (Claims)
  through 5010 (Member Services) in the order above.
- **`department-info` client scope** — a User Attribute → token claim
  mapper putting each user's `department` attribute into a `department`
  claim on every token. Matches `KEYCLOAK_DEPARTMENT_CLAIM` in both
  apps' `config.py`.
- **`central-llm-api-audience` client scope** — an audience mapper
  adding `central-llm-api` to every token's `aud`. Matches
  `KEYCLOAK_AUDIENCE` in the Gateway's `config.py`.
- **20 realm roles** (2 per department, e.g. `claims-analyst` /
  `claims-supervisor`), matching the convention already used in
  `generate_seed_data.py`.
- **10 test users**, one per department (`claims.tester`,
  `priorauth.tester`, ...), each with the department attribute set and
  the analyst-level role assigned. Password for all: `ChangeMe123!`
  — **rotate or delete these before anything resembling production.**
- **`profile`, `email`, `roles`, `web-origins`, `acr`** client scopes
  declared explicitly (see "Real bug found" below for why).

## Importing it

```bash
# One-time import into an existing Keycloak:
bin/kc.sh import --file /path/to/realm-export.json

# Or, auto-import at every startup (dev):
mkdir -p data/import
cp realm-export.json data/import/
bin/kc.sh start-dev --import-realm
```

## How this was validated

Hand-writing Keycloak realm JSON is easy to get subtly wrong in ways
that only show up at login time, so this wasn't just checked for valid
JSON syntax:

1. Downloaded and ran a real Keycloak 26.0.7 instance.
2. Imported this exact file with `kc.sh import`.
3. **Real bug found this way**: a bulk realm import does *not*
   auto-create Keycloak's built-in client scopes the way creating a
   realm through the admin console does. The first version of this
   file only declared the two custom scopes and referenced
   `profile`/`email`/`roles`/`web-origins`/`acr` by name on each
   client — Keycloak logged `Referenced client scope 'profile' doesn't
   exist. Ignoring` for every single one and silently dropped them.
   Every token would have been missing `preferred_username`, `email`,
   and `realm_access.roles`. Fixed by declaring all five built-in
   scopes explicitly with their standard mappers — re-imported with
   zero warnings.
4. Started Keycloak with the fixed realm, obtained a **real signed
   access token** for `claims.tester` via the actual token endpoint,
   and decoded it — confirmed `department: CLAIMS`,
   `aud: central-llm-api`, `azp: claims-chatbot-pkce`,
   `preferred_username`, `email`, and `realm_access.roles` are all
   present and correct.
5. Ran that real token through **the Gateway's actual
   `decode_and_validate()`** (not a test fixture) pointed at the live
   JWKS endpoint — accepted.
6. Ran the same token through **the Claims chatbot's actual
   `verify_token()`** — accepted, `azp` check passed.
7. Confirmed the negative case still holds with a real token: a
   Claims-department token evaluated against a `BILLING` request
   correctly fails the department check the Gateway enforces.

(Step 4 required temporarily flipping `directAccessGrantsEnabled` on
one client in the *running test instance* to mint a token without
scripting a full browser redirect — that flag is `false` in the
shipped `realm-export.json`, as it should be for a PKCE-only client.)

## Recommended bring-up order

1. **Database**: `mysql -u root -p < database/schema.sql && mysql -u root -p < database/seed_data.sql`
2. **Keycloak**: import this realm (see above), confirm
   `http://localhost:8080/realms/health-ai-central/.well-known/openid-configuration`
   responds
3. **Central LLM Gateway**: set `KEYCLOAK_ISSUER`,
   `KEYCLOAK_AUDIENCE=central-llm-api` in its `.env`, start it,
   `DEV_BYPASS_AUTH=false`
4. **Claims chatbot**: set `KEYCLOAK_ISSUER`,
   `KEYCLOAK_CLIENT_ID=claims-chatbot-pkce`,
   `OAUTH_REDIRECT_URI=http://localhost:5001/auth/callback` in its
   `.env`, `DEV_BYPASS_AUTH=false`, start it
5. Visit `http://localhost:5001/`, log in as `claims.tester` /
   `ChangeMe123!` — this is a real PKCE authorization-code login now,
   not the dev bypass.

## Regenerating

`generate_realm.py` produces `realm-export.json` deterministically —
edit the `DEPARTMENTS` list (add a department, change a port, add
roles) and re-run it rather than hand-editing the JSON.
