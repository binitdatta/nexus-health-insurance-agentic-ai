from app.langgraph_flow.graph import run_chat_turn


def test_data_lookup_retrieves_real_report_and_renders_table(app, logged_in_client, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup",
        "confidence": 0.95,
        "entities": {"report_ref": "RPT700000"},
        "suggested_render": "table",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 100, "completion_tokens": 20, "total_cost_usd": 0.0001, "latency_ms": 50},
    }

    captured_context = {}

    def fake_respond(payload):
        captured_context["retrieved_context"] = payload["retrieved_context"]
        return {
            "answer_markdown": "RPT700000 details below.",
            "render": {"type": "table", "spec": {"columns": ["report_ref"], "rows": [["RPT700000"]]}},
            "citations": ["report:RPT700000"],
            "confidence": 0.9,
            "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 300, "completion_tokens": 80, "total_cost_usd": 0.002, "latency_ms": 400},
        }

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        session["chat_session_id"] = "test-session-1"

        final_state = run_chat_turn(session_id="test-session-1", message="What does report RPT700000 say?")

    assert final_state["intent"] == "data_lookup"
    # Retrieval must have actually queried the real seeded management_reports table
    assert len(captured_context["retrieved_context"]) == 1
    assert captured_context["retrieved_context"][0]["source"] == "report:RPT700000"
    assert "report_ref=RPT700000" in captured_context["retrieved_context"][0]["content"]
    assert "Call Center SLA Summary" in captured_context["retrieved_context"][0]["content"]  # real seeded title
    assert final_state["render"]["type"] == "table"
    assert final_state.get("hitl") is None  # no HITL for a lookup


def test_data_lookup_tolerates_report_id_synonym_from_llm(app, canned_gateway_responses):
    """Regression test for the same class of bug found across every
    other department chatbot: the LLM sometimes names the identifier
    entity 'report_id' instead of 'report_ref'. retrieve_node must not
    silently fall through to an unfiltered query when that happens."""
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup", "confidence": 0.9,
        "entities": {"report_id": "RPT700000"},  # deliberately the "wrong" key
        "suggested_render": "table",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 90, "completion_tokens": 15, "total_cost_usd": 0.0001, "latency_ms": 40},
    }
    captured = {}

    def fake_respond(payload):
        captured["ctx"] = payload["retrieved_context"]
        return {"answer_markdown": "Found it.", "render": {"type": "table"}, "citations": [], "confidence": 0.9,
                "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 200, "completion_tokens": 50, "total_cost_usd": 0.001, "latency_ms": 300}}

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        run_chat_turn(session_id="test-session-2", message="What does report RPT700000 say?")

    assert len(captured["ctx"]) == 1
    assert captured["ctx"][0]["source"] == "report:RPT700000"


def test_data_lookup_resolves_covers_dept_from_department_code(app, canned_gateway_responses):
    """retrieve_node must resolve a department code/name entity to the
    real covers_dept_id before filtering — a human or the LLM will say
    'Claims', not the internal numeric FK."""
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup", "confidence": 0.85,
        "entities": {"covers_dept_code": "CLAIMS"},
        "suggested_render": "table",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 90, "completion_tokens": 15, "total_cost_usd": 0.0001, "latency_ms": 40},
    }
    captured = {}

    def fake_respond(payload):
        captured["ctx"] = payload["retrieved_context"]
        return {"answer_markdown": "Here are the Claims reports.", "render": {"type": "table"}, "citations": [], "confidence": 0.8,
                "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 200, "completion_tokens": 50, "total_cost_usd": 0.001, "latency_ms": 300}}

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        run_chat_turn(session_id="test-session-3", message="Show me reports covering Claims")

    from app import repository
    claims_dept_id = repository.get_dept_id("CLAIMS")
    assert len(captured["ctx"]) > 0
    # every retrieved row's content must reference the resolved numeric id, not the literal string "CLAIMS"
    for item in captured["ctx"]:
        assert f"covers_dept_id={claims_dept_id}" in item["content"]


def test_policy_question_retrieves_knowledge_docs(app, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "policy_question", "confidence": 0.9, "entities": {}, "suggested_render": "text",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 90, "completion_tokens": 15, "total_cost_usd": 0.0001, "latency_ms": 40},
    }
    captured = {}

    def fake_respond(payload):
        captured["ctx"] = payload["retrieved_context"]
        return {"answer_markdown": "Per policy...", "render": {"type": "text"}, "citations": [], "confidence": 0.8,
                "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 200, "completion_tokens": 50, "total_cost_usd": 0.001, "latency_ms": 300}}

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        final_state = run_chat_turn(session_id="test-session-4", message="How are our KPIs defined?")

    assert final_state["intent"] == "policy_question"
    assert len(captured["ctx"]) > 0
    assert all(c["type"].startswith("knowledge_doc:") for c in captured["ctx"])


def test_create_record_includes_department_reference_for_grounding(app, canned_gateway_responses):
    """The behavior unique to this chatbot: since covers_dept_id is an
    internal FK no one can guess, retrieve_node must always include
    the real department code->id mapping in context for create/update
    intents, not just whatever the search happens to find."""
    canned_gateway_responses["intent"] = {
        "intent": "create_record", "confidence": 0.8,
        "entities": {}, "suggested_render": "none",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 80, "completion_tokens": 20, "total_cost_usd": 0.0001, "latency_ms": 45},
    }
    canned_gateway_responses["respond"] = {
        "answer_markdown": "Drafting a report.", "render": {"type": "text"}, "citations": [], "confidence": 0.7,
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 250, "completion_tokens": 60, "total_cost_usd": 0.0015, "latency_ms": 350},
    }
    canned_gateway_responses["hitl-draft"] = {
        "proposed_payload": {"report_title": "New Report"},
        "missing_fields": ["report_ref", "covers_dept_id", "report_period", "report_date"],
        "rationale": "test", "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 100, "completion_tokens": 30, "total_cost_usd": 0.0005, "latency_ms": 200},
    }

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        final_state = run_chat_turn(session_id="test-session-5", message="Draft a new report for the Billing department")

    dept_ref_items = [c for c in final_state["retrieved_context"] if c["source"] == "department_reference"]
    assert len(dept_ref_items) == 1
    assert "CLAIMS=1" in dept_ref_items[0]["content"]
    assert "BILLING=" in dept_ref_items[0]["content"]


def test_hitl_draft_sends_real_schema_not_invented_fields(app, canned_gateway_responses):
    """Same class of bug caught across every other department chatbot:
    the HITL draft prompt must only ever be told about real
    management_reports columns, never invented field names with
    nowhere to be saved."""
    canned_gateway_responses["intent"] = {
        "intent": "update_record", "confidence": 0.9,
        "entities": {"report_ref": "RPT700000"}, "suggested_render": "none",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 50, "completion_tokens": 10, "total_cost_usd": 0.0001, "latency_ms": 30},
    }
    canned_gateway_responses["respond"] = {
        "answer_markdown": "Draft ready.", "render": {"type": "text"}, "citations": [], "confidence": 0.8,
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 100, "completion_tokens": 30, "total_cost_usd": 0.0005, "latency_ms": 200},
    }

    captured = {}

    def fake_hitl(payload):
        captured["allowed_fields"] = payload.get("allowed_fields")
        captured["required_fields"] = payload.get("required_fields")
        captured["entity_type"] = payload.get("entity_type")
        return {
            "proposed_payload": {"report_ref": "RPT700000", "report_title": "Updated"},
            "missing_fields": [],
            "rationale": "test",
            "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 80, "completion_tokens": 20, "total_cost_usd": 0.0003, "latency_ms": 150},
        }

    canned_gateway_responses["hitl-draft"] = fake_hitl

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        run_chat_turn(session_id="test-session-6", message="Update report RPT700000's title")

    from app import repository
    assert captured["entity_type"] == "management_reports"
    assert captured["allowed_fields"] == repository.REPORT_COLUMNS
    assert captured["required_fields"] == repository.REQUIRED_REPORT_FIELDS
    for invented in ("executive_summary", "action_items", "distribution_list"):
        assert invented not in captured["allowed_fields"]


def test_usage_events_accumulate_across_nodes(app, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "dashboard_metric", "confidence": 0.9, "entities": {}, "suggested_render": "chart",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 50, "completion_tokens": 10, "total_cost_usd": 0.00005, "latency_ms": 30},
    }
    canned_gateway_responses["respond"] = {
        "answer_markdown": "Here's the breakdown.", "render": {"type": "chart", "spec": {}}, "citations": [], "confidence": 0.8,
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 180, "completion_tokens": 45, "total_cost_usd": 0.0009, "latency_ms": 250},
    }

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "MANAGEMENT", "roles": []}
        final_state = run_chat_turn(session_id="test-session-7", message="How many reports do we have per department?")

    assert len(final_state["usage_events"]) == 2
    total_cost = sum(e["total_cost_usd"] for e in final_state["usage_events"])
    assert round(total_cost, 5) == round(0.00005 + 0.0009, 5)
