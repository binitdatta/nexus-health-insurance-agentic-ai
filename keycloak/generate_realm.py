#!/usr/bin/env python3
"""
Generates realm-export.json for the health-ai-central realm.

Produces exactly what central-llm-gateway and claims-chatbot already
expect, so importing this and turning DEV_BYPASS_AUTH off should just
work:
  - a "department" protocol mapper (user attribute -> token claim),
    matching KEYCLOAK_DEPARTMENT_CLAIM in both apps' config.py
  - a "central-llm-api" audience mapper, matching KEYCLOAK_AUDIENCE in
    the Gateway's config.py
  - one public PKCE client per department, named "<code>-chatbot-pkce"
    to match KEYCLOAK_CLIENT_ID in each chatbot's config.py
  - two realm roles per department (analyst/manager-style), matching
    the ROLE_BY_DEPT convention used in generate_seed_data.py
  - one test user per department with the department attribute set and
    the analyst role assigned, so you can log in immediately
"""
import json

DEPARTMENTS = [
    ("CLAIMS", "Claims", 5001, ["claims-analyst", "claims-supervisor"]),
    ("PRIORAUTH", "Prior Authorization", 5002, ["pa-reviewer", "pa-medical-director"]),
    ("NURSING", "Nursing", 5003, ["care-nurse", "nurse-manager"]),
    ("CALLCENTER", "Call Center", 5004, ["call-agent", "call-center-lead"]),
    ("BILLING", "Billing", 5005, ["billing-specialist", "billing-manager"]),
    ("FACPROV", "Facility & Providers", 5006, ["provider-relations", "network-manager"]),
    ("ADJUDICATION", "Adjudication", 5007, ["adjudicator", "adjudication-lead"]),
    ("FINANCE", "Finance", 5008, ["finance-analyst", "controller"]),
    ("MANAGEMENT", "Management", 5009, ["dept-director", "vp"]),
    ("MEMBERSVC", "Member Services", 5010, ["member-svc-rep", "member-svc-lead"]),
]

TEST_PASSWORD = "ChangeMe123!"

realm_roles = []
for code, _name, _port, roles in DEPARTMENTS:
    for r in roles:
        realm_roles.append({"name": r, "description": f"{r} ({code})"})

client_scopes = [
    # --- Standard scopes, declared explicitly ---------------------------
    # A bulk realm import (unlike creating a realm through the admin
    # console) does NOT auto-create Keycloak's built-in client scopes —
    # confirmed by actually importing this realm and watching Keycloak
    # log "Referenced client scope 'profile' doesn't exist. Ignoring"
    # for every client. Without these, tokens would be missing
    # preferred_username, email, and realm_access.roles entirely.
    {
        "name": "profile",
        "description": "OpenID Connect built-in scope: profile",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "true"},
        "protocolMappers": [
            {
                "name": "username", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
                "consentRequired": False,
                "config": {"userinfo.token.claim": "true", "user.attribute": "username", "id.token.claim": "true",
                           "access.token.claim": "true", "claim.name": "preferred_username", "jsonType.label": "String"},
            },
            {
                "name": "given name", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
                "consentRequired": False,
                "config": {"userinfo.token.claim": "true", "user.attribute": "firstName", "id.token.claim": "true",
                           "access.token.claim": "true", "claim.name": "given_name", "jsonType.label": "String"},
            },
            {
                "name": "family name", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
                "consentRequired": False,
                "config": {"userinfo.token.claim": "true", "user.attribute": "lastName", "id.token.claim": "true",
                           "access.token.claim": "true", "claim.name": "family_name", "jsonType.label": "String"},
            },
        ],
    },
    {
        "name": "email",
        "description": "OpenID Connect built-in scope: email",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "true"},
        "protocolMappers": [
            {
                "name": "email", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
                "consentRequired": False,
                "config": {"userinfo.token.claim": "true", "user.attribute": "email", "id.token.claim": "true",
                           "access.token.claim": "true", "claim.name": "email", "jsonType.label": "String"},
            },
            {
                "name": "email verified", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-property-mapper",
                "consentRequired": False,
                "config": {"userinfo.token.claim": "true", "user.attribute": "emailVerified", "id.token.claim": "true",
                           "access.token.claim": "true", "claim.name": "email_verified", "jsonType.label": "boolean"},
            },
        ],
    },
    {
        "name": "roles",
        "description": "OpenID Connect built-in scope: roles",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "true"},
        "protocolMappers": [
            {
                "name": "realm roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
                "consentRequired": False,
                "config": {"user.attribute": "foo", "access.token.claim": "true", "claim.name": "realm_access.roles",
                           "jsonType.label": "String", "multivalued": "true", "id.token.claim": "false"},
            },
            {
                "name": "audience resolve", "protocol": "openid-connect", "protocolMapper": "oidc-audience-resolve-mapper",
                "consentRequired": False, "config": {},
            },
        ],
    },
    {
        "name": "web-origins",
        "description": "OpenID Connect built-in scope: web-origins",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
        "protocolMappers": [
            {
                "name": "allowed web origins", "protocol": "openid-connect", "protocolMapper": "oidc-allowed-origins-mapper",
                "consentRequired": False, "config": {},
            },
        ],
    },
    {
        "name": "acr",
        "description": "OpenID Connect built-in scope: acr",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
        "protocolMappers": [
            {
                "name": "acr loa level", "protocol": "openid-connect", "protocolMapper": "oidc-acr-mapper",
                "consentRequired": False,
                "config": {"id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"},
            },
        ],
    },
    # --- Custom scopes for this platform ---------------------------------
    {
        "name": "department-info",
        "description": "Adds the user's department (User Attribute) as a 'department' token claim.",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
        "protocolMappers": [
            {
                "name": "department-attribute-mapper",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-attribute-mapper",
                "consentRequired": False,
                "config": {
                    "user.attribute": "department",
                    "claim.name": "department",
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                },
            }
        ],
    },
    {
        "name": "central-llm-api-audience",
        "description": "Adds 'central-llm-api' to the token audience so the Gateway accepts relayed tokens.",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
        "protocolMappers": [
            {
                "name": "central-llm-api-audience-mapper",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "consentRequired": False,
                "config": {
                    "included.custom.audience": "central-llm-api",
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            }
        ],
    },
]

DEFAULT_SCOPES = ["web-origins", "acr", "profile", "roles", "email", "department-info", "central-llm-api-audience"]
OPTIONAL_SCOPES = []  # offline_access/address/phone/microprofile-jwt intentionally omitted — unused by any app here

clients = []
users = []
for code, name, port, roles in DEPARTMENTS:
    client_id = f"{code.lower()}-chatbot-pkce"
    clients.append({
        "clientId": client_id,
        "name": f"{name} Chatbot (PKCE public client)",
        "enabled": True,
        "publicClient": True,
        "protocol": "openid-connect",
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [f"http://localhost:{port}/auth/callback"],
        "webOrigins": [f"http://localhost:{port}"],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": f"http://localhost:{port}/*",
        },
        "defaultClientScopes": DEFAULT_SCOPES,
        "optionalClientScopes": OPTIONAL_SCOPES,
    })

    analyst_role = roles[0]
    username = f"{code.lower()}.tester"
    users.append({
        "username": username,
        "enabled": True,
        "emailVerified": True,
        "firstName": name.split(" ")[0],
        "lastName": "Tester",
        "email": f"{username}@example.com",
        "credentials": [{"type": "password", "value": TEST_PASSWORD, "temporary": False}],
        "attributes": {"department": [code]},
        "realmRoles": [analyst_role],
    })

realm = {
    "realm": "health-ai-central",
    "enabled": True,
    "displayName": "Nationwide Health Insurance — Central Realm",
    "sslRequired": "external",
    "registrationAllowed": False,
    "resetPasswordAllowed": True,
    "editUsernameAllowed": False,
    "bruteForceProtected": True,
    "accessTokenLifespan": 300,
    "ssoSessionIdleTimeout": 1800,
    "ssoSessionMaxLifespan": 36000,
    "roles": {"realm": realm_roles},
    "clientScopes": client_scopes,
    "clients": clients,
    "users": users,
}

with open("realm-export.json", "w") as f:
    json.dump(realm, f, indent=2)

print(f"Wrote realm-export.json")
print(f"Departments/clients: {len(clients)}")
print(f"Realm roles: {len(realm_roles)}")
print(f"Test users: {len(users)}  (password for all: {TEST_PASSWORD})")
