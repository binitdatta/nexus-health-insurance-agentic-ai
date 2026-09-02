"""
Central content registry for the Nexus Health Insurance AI Platform Hub.

The navbar, Home page chatbot cards, Training dropdown, Training index,
Architecture references, and HIPAA GPU pricing all read from this module.
"""


# ============================================================================
# DEPARTMENT CHATBOTS
# ============================================================================

CHATBOTS = [
    {
        "dept_code": "CLAIMS",
        "name": "Claims",
        "port": 5001,
        "description": "Claim status lookups, denial reasons, appeals via human-in-the-loop.",
        "icon": "bi-file-earmark-medical",
    },
    {
        "dept_code": "PRIORAUTH",
        "name": "Prior Authorization",
        "port": 5002,
        "description": "PA request status, urgency-tiered review, medical necessity policy Q&A.",
        "icon": "bi-clipboard-check",
    },
    {
        "dept_code": "NURSING",
        "name": "Nursing",
        "port": 5003,
        "description": "Care management cases, acuity scoring, discharge planning.",
        "icon": "bi-heart-pulse",
    },
    {
        "dept_code": "CALLCENTER",
        "name": "Call Center",
        "port": 5004,
        "description": "Call logs, CSAT tracking, escalation and identity-verification policy.",
        "icon": "bi-telephone",
    },
    {
        "dept_code": "BILLING",
        "name": "Billing",
        "port": 5005,
        "description": "Invoice status, payment plans, write-off and grace-period policy.",
        "icon": "bi-receipt",
    },
    {
        "dept_code": "FACPROV",
        "name": "Facility & Providers",
        "port": 5006,
        "description": "Provider directory, network status, credentialing workflow.",
        "icon": "bi-hospital",
    },
    {
        "dept_code": "ADJUDICATION",
        "name": "Adjudication",
        "port": 5007,
        "description": "Rule-engine decisions, bundling edits, append-only adjudication history.",
        "icon": "bi-diagram-3",
    },
    {
        "dept_code": "FINANCE",
        "name": "Finance",
        "port": 5008,
        "description": "GL transactions, vendor payment approvals, loss ratio reporting.",
        "icon": "bi-cash-coin",
    },
    {
        "dept_code": "MANAGEMENT",
        "name": "Management",
        "port": 5009,
        "description": "Cross-department KPI reports, quarterly reviews, escalation policy.",
        "icon": "bi-graph-up-arrow",
    },
    {
        "dept_code": "MEMBERSVC",
        "name": "Member Services",
        "port": 5010,
        "description": "ID card reissue, address changes, grievance intake, open enrollment.",
        "icon": "bi-person-lines-fill",
    },
]


# ============================================================================
# SHARED PLATFORM PORTS
# ============================================================================

GATEWAY_PORT = 8000
KEYCLOAK_PORT = 8080


# ============================================================================
# TRAINING TOPICS
# ============================================================================

TRAINING_TOPICS = [
    {
        "slug": "langgraph-multi-department-platform",
        "category": "Architecture",
        "title": "Building a 10-Department Agentic AI Platform with LangGraph",
        "length": "18-25 min",
        "audience": "Developers evaluating LangGraph for a real multi-tenant product, not a toy demo",
        "hook": (
            "Ten department chatbots, one shared pipeline shape "
            "(classify → retrieve → synthesize → conditional HITL draft), "
            "and what stayed the same vs. what genuinely had to change per department."
        ),
        "outline": [
            "Why a shared LangGraph pipeline shape across departments, and where it broke down "
            "(Facility & Providers has no member_id; Adjudication has no unique key at all)",
            "Walk the actual graph: classify_intent → retrieve → synthesize → conditional hitl_draft, "
            "with the real Mermaid diagram this project generated",
            "Why retrieval lives in each chatbot, not the central Gateway — and what that buys you "
            "(department-scoped SQL, no PHI in a shared service beyond what's explicitly retrieved)",
            "The entity-key mismatch bug: LLM said 'claim_id', code expected 'claim_number' — "
            "and the fix (tolerant aliasing + explicit schema examples in the prompt)",
            "Show the real test suite catching a real regression live on screen",
        ],
        "files_to_show": [
            "app/langgraph_flow/graph.py",
            "app/langgraph_flow/nodes.py",
            "tests/test_langgraph_flow.py",
        ],
    },

    {
        "slug": "keycloak-pkce-enterprise-ai",
        "category": "Security",
        "title": "Keycloak 26 PKCE for Enterprise AI Chatbots (with a Real Bug I Found)",
        "length": "15-20 min",
        "audience": "Backend/platform engineers adding real auth to an LLM-backed app for the first time",
        "hook": (
            "A bulk Keycloak realm import silently drops the built-in client scopes the admin "
            "console creates automatically — every token would have been missing "
            "preferred_username, email, and roles. Here's how that was caught before it shipped."
        ),
        "outline": [
            "Why token relay (not a shared service credential) for department-scoped RBAC",
            "The department claim + audience mapper pattern that ties a user's token to both "
            "their department and the central Gateway",
            "The bug: importing a realm via kc.sh doesn't auto-create profile/email/roles scopes "
            "the way the admin console does — reproduced live",
            "Minting a real signed token and running it through the actual application code "
            "(not a mock) to prove the fix",
            "The negative case that matters most: a Claims token used against Billing — "
            "proving RBAC fails closed, not open",
        ],
        "files_to_show": [
            "realm-export.json",
            "generate_realm.py",
            "app/auth.py (Gateway)",
            "app/security/jwt_verify.py (chatbot)",
        ],
    },

    {
        "slug": "central-llm-gateway-pattern",
        "category": "Architecture",
        "title": "The Central LLM Gateway Pattern: One Chokepoint for Cost, Audit, and Control",
        "length": "15-20 min",
        "audience": (
            "Teams with more than one LLM-backed feature who are tired of scattered API keys "
            "and no cost visibility"
        ),
        "hook": (
            "Ten chatbots, one place that ever talks to Anthropic — full request/response "
            "payloads, token counts, and cost logged to both a flat file and MySQL for every single call."
        ),
        "outline": [
            "Why retrieval stays OUT of the Gateway — it only ever does the LLM call",
            "Forced tool-use instead of prompt-engineered JSON: why, and the schema-passing fix "
            "that stops the LLM from inventing fields",
            "Dual logging: what goes to the flat file vs. what goes to llm_call_log, and why both",
            "Cost calculation from token counts — and why the pricing table is data, not code",
            "Live demo: watching a real call show up in the dashboard in real time",
        ],
        "files_to_show": [
            "app/anthropic_client.py",
            "app/blueprints/llm_gateway.py",
            "app/prompts.py",
        ],
    },

    {
        "slug": "human-in-the-loop-ai-writes",
        "category": "Architecture",
        "title": "Human-in-the-Loop: Letting an LLM Propose Writes Without Letting It Write",
        "length": "12-18 min",
        "audience": "Anyone building an AI agent that needs to eventually touch a production database",
        "hook": (
            "The AI drafts a record. A human approves, edits, or rejects it. Two different real "
            "bugs — one where the LLM invented fields that didn't exist in the schema, one where "
            "a re-adjudicated claim would have silently overwritten history — shaped how this actually works."
        ),
        "outline": [
            "Inline (in-chat) approval vs. the standalone review queue — same API, two surfaces",
            "The schema-passing fix: why the Gateway now gets told the real column list before drafting anything",
            "Update-vs-insert isn't universal: Adjudication always inserts "
            "(append-only decision log) because claim_number isn't unique there",
            "Required-field validation that blocks a bad approval instead of half-committing garbage",
            "What the review queue actually shows a human, and why that matters as much as the API",
        ],
        "files_to_show": [
            "app/repository.py (approve_hitl_task)",
            "app/blueprints/hitl.py",
            "tests/test_hitl.py",
        ],
    },

    {
        "slug": "real-bugs-found-testing-llm-app",
        "category": "Testing",
        "title": "Real Bugs I Found Testing an LLM-Backed Enterprise App (Not Hypotheticals)",
        "length": "20-25 min",
        "audience": "Anyone who thinks 'testing an AI app' means mocking the LLM and calling it done",
        "hook": (
            "A SQL apostrophe bug that silently killed the last 3 statements in a file. "
            "A Flask session gotcha that looked like a broken RBAC check but wasn't. "
            "An anthropic/httpx version pin that broke on first real call."
        ),
        "outline": [
            "The httpx/anthropic version pin bug: caught by actually calling the real API",
            "The Flask nested-session-mutation bug: why a failing test isn't automatically an app bug",
            "The unescaped-apostrophe SQL bug that broke silently mid-script",
            "The claim_id vs. claim_number entity-key bug — caught via a live UI test",
            "Why 'mock the LLM, test everything else for real' was the actual testing strategy",
        ],
        "files_to_show": [
            "tests/conftest.py (canned_gateway_responses fixture)",
            "fix_membersvc_knowledge_docs.sql",
        ],
    },

    {
        "slug": "hipaa-and-llms-what-changes",
        "category": "Compliance",
        "title": "HIPAA and LLMs: What Actually Changes in Your Architecture",
        "length": "20-30 min",
        "audience": "Engineers who've been told 'make sure it's HIPAA compliant' with no further guidance",
        "hook": (
            "A signed BAA is a legal precondition, not a technical checkbox — and it doesn't "
            "cover every API feature automatically."
        ),
        "outline": [
            "Why 'no BAA = no lawful basis to send PHI to a vendor' regardless of code quality",
            "BAA coverage is per-feature, not blanket",
            "Auditing this platform's own architecture: what passed and what didn't",
            "Managed API + BAA vs. self-hosting vs. hybrid tokenization/minimization",
            "What's cheap to fix vs. what's an organizational decision",
        ],
        "files_to_show": [
            "This platform's /hipaa-compliance page",
        ],
    },

    {
        "slug": "self-hosting-ollama-cost-reality",
        "category": "Compliance",
        "title": "Self-Hosting an Open-Source LLM with Ollama: What It Actually Costs",
        "length": "15-20 min",
        "audience": (
            "Teams considering self-hosting for compliance reasons and wanting real numbers "
            "before committing"
        ),
        "hook": (
            "Renting a GPU to keep PHI off a third-party API sounds simple until you price it out."
        ),
        "outline": [
            "Why self-hosting solves the BAA problem differently, not automatically better",
            "VRAM math: what model size actually fits on which GPU",
            "Real 2026 pricing across specialist clouds vs. hyperscalers",
            "Always-on vs. business-hours-only cost",
            "Re-validating structured tool-use against the local model",
        ],
        "files_to_show": [
            "This platform's /hipaa-compliance page (GPU cost table)",
        ],
    },

    {
        "slug": "rbac-for-ai-jwt-claims",
        "category": "Security",
        "title": "RBAC for AI Agents: Department-Scoped Access Control with JWT Claims",
        "length": "12-15 min",
        "audience": (
            "Developers who've only ever done RBAC for CRUD apps, "
            "not for an AI agent making tool calls"
        ),
        "hook": (
            "The chatbot never decides who can see what — a signed JWT claim does, "
            "checked in two completely separate services."
        ),
        "outline": [
            "Why the department claim lives on the Keycloak side",
            "The azp authorized-party check",
            "Gateway department check + chatbot azp check",
            "Live negative test across departments",
        ],
        "files_to_show": [
            "app/auth.py (Gateway)",
            "app/security/jwt_verify.py",
            "app/security/decorators.py",
        ],
    },

    {
        "slug": "testing-strategy-mock-vs-real",
        "category": "Testing",
        "title": "Testing AI Agents: What to Mock and What to Run for Real",
        "length": "15-20 min",
        "audience": (
            "Developers unsure where to draw the mocking line "
            "in an LLM-backed app's test suite"
        ),
        "hook": (
            "Every test in this platform mocks the LLM call itself but runs everything else — "
            "retrieval, auth, database writes — against real infrastructure."
        ),
        "outline": [
            "Why the LLM call is worth mocking",
            "Why retrieval SQL should run against real seeded data",
            "The canned_gateway_responses fixture pattern",
            "Real Keycloak, signed tokens, and JWKS validation",
            "Fresh-extraction-and-install testing",
        ],
        "files_to_show": [
            "tests/conftest.py",
            "tests/test_langgraph_flow.py",
        ],
    },

    {
        "slug": "zero-to-ten-chatbots-scaling-a-pattern",
        "category": "Architecture",
        "title": "From Zero to 10 Chatbots: Scaling One Pattern Across Departments",
        "length": "20-25 min",
        "audience": (
            "Anyone about to copy-paste a working service 9 more times "
            "and wondering what will go wrong"
        ),
        "hook": (
            "The same identity-string bug got copied forward and caught in every single one "
            "of nine copies."
        ),
        "outline": [
            "What genuinely stayed identical across all 10 chatbots",
            "What had to change every time",
            "Why copying a working service doesn't mean copying a correct one",
            "Structural exceptions across departments",
            "A pre-flight checklist for chatbot #11",
        ],
        "files_to_show": [
            "Any two department chatbots' repository.py side by side",
        ],
    },

    {
        "slug": "forced-tool-use-structured-output",
        "category": "Architecture",
        "title": "Structured Output from LLMs: Why Forced Tool-Use Beats Prompt Engineering",
        "length": "10-15 min",
        "audience": (
            "Developers currently asking an LLM to 'please respond in JSON' "
            "and parsing it with regex"
        ),
        "hook": (
            "Every operation in this platform's Gateway forces a specific tool call "
            "instead of asking nicely for JSON."
        ),
        "outline": [
            "Failure modes of prompt-engineered JSON",
            "Forcing tool_choice to a specific tool",
            "Why forced tool-use still needs schema validation",
            "Why model choice matters for structured-output reliability",
        ],
        "files_to_show": [
            "app/prompts.py",
            "app/anthropic_client.py",
        ],
    },

    {
        "slug": "mysql-schema-multi-tenant-audit-logging",
        "category": "Architecture",
        "title": "Designing a MySQL Schema for Multi-Tenant AI Audit Logging",
        "length": "12-18 min",
        "audience": (
            "Backend engineers designing the data layer under an AI feature "
            "for the first time"
        ),
        "hook": (
            "Every LLM call, HTTP call, and human approval decision is logged so the platform "
            "can answer who did what, when, and what it cost."
        ),
        "outline": [
            "departments as the tenancy anchor",
            "llm_call_log vs. http_call_log",
            "hitl_task_queue",
            "cost_summary_daily",
            "Why kpi_summary uses a JSON column",
        ],
        "files_to_show": [
            "schema.sql",
        ],
    },
]


# ============================================================================
# GPU PRICING
# ============================================================================

GPU_PRICING = [
    {
        "gpu": "RTX 4090 24GB",
        "cheapest_ondemand": "$0.34/hr",
        "provider": "RunPod Community Cloud",
        "hyperscaler_ref": "not offered by AWS/Azure/GCP as a discrete SKU",
        "fits": "7B–13B models, 4-bit quantized",
    },
    {
        "gpu": "A100 40GB",
        "cheapest_ondemand": "$1.99/hr",
        "provider": "Lambda Labs",
        "hyperscaler_ref": "GCP a2-highgpu-1g ≈ $3.67/hr (whole VM)",
        "fits": "13B–34B models, 4-bit quantized",
    },
    {
        "gpu": "A100 80GB",
        "cheapest_ondemand": "$1.49/hr",
        "provider": "Jarvislabs / RunPod",
        "hyperscaler_ref": "AWS/Azure ≈ $3.40–$3.43/hr (8-GPU instance only)",
        "fits": "70B models, 4-bit quantized (e.g. Llama 3.3 70B)",
    },
    {
        "gpu": "H100 SXM 80GB",
        "cheapest_ondemand": "$1.99–$2.69/hr",
        "provider": "RunPod",
        "hyperscaler_ref": "AWS P5 ≈ $6.88/hr, Azure ND H100 v5 ≈ $12.29/hr",
        "fits": "70B models at higher throughput, or larger MoE models quantized",
    },
    {
        "gpu": "B200 SXM6",
        "cheapest_ondemand": "$5.29–$6.02/hr",
        "provider": "Lambda / Spheron",
        "hyperscaler_ref": "AWS P6 ≈ $14.24/hr",
        "fits": "Largest current open-weight models, headroom for higher concurrency",
    },
]