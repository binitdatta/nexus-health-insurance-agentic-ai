from flask import Blueprint, render_template

from . import content

hub_bp = Blueprint("hub", __name__)


@hub_bp.get("/")
def home():
    return render_template("home.html")


@hub_bp.get("/architecture")
def architecture():
    return render_template("architecture.html")


@hub_bp.get("/hipaa-compliance")
def hipaa_compliance():
    return render_template("hipaa.html", gpu_pricing=content.GPU_PRICING)


@hub_bp.get("/training")
def training_index():
    return render_template("training_index.html")


# Independent training pages
@hub_bp.get("/training/building-10-department-agentic-ai-platform-langgraph")
def building_10_department_agentic_ai_platform_langgraph():
    return render_template("training/building-10-department-agentic-ai-platform-langgraph.html")

@hub_bp.get("/training/keycloak-26-pkce-enterprise-ai-chatbots")
def keycloak_26_pkce_enterprise_ai_chatbots():
    return render_template("training/keycloak-26-pkce-enterprise-ai-chatbots.html")

@hub_bp.get("/training/central-llm-gateway-pattern")
def central_llm_gateway_pattern():
    return render_template("training/central-llm-gateway-pattern.html")

@hub_bp.get("/training/human-in-the-loop-ai-propose-humans-write")
def human_in_the_loop_ai_propose_humans_write():
    return render_template("training/human-in-the-loop-ai-propose-humans-write.html")

@hub_bp.get("/training/real-bugs-found-testing-llm-app")
def real_bugs_found_testing_llm_app():
    return render_template("training/real-bugs-found-testing-llm-app.html")

@hub_bp.get("/training/hipaa-and-llms-what-changes")
def hipaa_and_llms_what_changes():
    return render_template("training/hipaa-and-llms-what-changes.html")

@hub_bp.get("/training/self-hosting-ollama-cost-reality")
def self_hosting_ollama_cost_reality():
    return render_template("training/self-hosting-ollama-cost-reality.html")

@hub_bp.get("/training/rbac-ai-agents-department-scoped-access-jwt")
def rbac_ai_agents_department_scoped_access_jwt():
    return render_template("training/rbac-ai-agents-department-scoped-access-jwt.html")

@hub_bp.get("/training/testing-ai-agents-what-to-mock-real")
def testing_ai_agents_what_to_mock_real():
    return render_template("training/testing-ai-agents-what-to-mock-real.html")

@hub_bp.get("/training/zero-to-10-chatbots-scaling-pattern")
def zero_to_10_chatbots_scaling_pattern():
    return render_template("training/zero-to-10-chatbots-scaling-pattern.html")

@hub_bp.get("/training/structured-output-forced-tool-use")
def structured_output_forced_tool_use():
    return render_template("training/structured-output-forced-tool-use.html")

@hub_bp.get("/training/mysql-schema-multitenant-ai-audit-logging")
def mysql_schema_multitenant_ai_audit_logging():
    return render_template("training/mysql-schema-multitenant-ai-audit-logging.html")
