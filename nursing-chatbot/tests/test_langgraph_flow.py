from app.langgraph_flow.graph import run_chat_turn


def test_data_lookup_retrieves_real_case_and_renders_table(app, logged_in_client, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup",
        "confidence": 0.95,
        "entities": {"case_number": "NC300000"},
        "suggested_render": "table",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 100, "completion_tokens": 20, "total_cost_usd": 0.0001, "latency_ms": 50},
    }

    captured_context = {}

    def fake_respond(payload):
        captured_context["retrieved_context"] = payload["retrieved_context"]
        return {
            "answer_markdown": "NC300000 details below.",
            "render": {"type": "table", "spec": {"columns": ["case_number"], "rows": [["NC300000"]]}},
            "citations": ["nursing_case:NC300000"],
            "confidence": 0.9,
            "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 300, "completion_tokens": 80, "total_cost_usd": 0.002, "latency_ms": 400},
        }

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "NURSING", "roles": []}
        session["chat_session_id"] = "test-session-1"

        final_state = run_chat_turn(session_id="test-session-1", message="What is the status of case NC300000?")

    assert final_state["intent"] == "data_lookup"
    # Retrieval must have actually queried the real seeded nursing_cases table
    assert len(captured_context["retrieved_context"]) == 1
    assert captured_context["retrieved_context"][0]["source"] == "nursing_case:NC300000"
    assert "case_number=NC300000" in captured_context["retrieved_context"][0]["content"]
    assert "MBR99226" in captured_context["retrieved_context"][0]["content"]  # real seeded member for NC300000
    assert final_state["render"]["type"] == "table"
    assert final_state.get("hitl") is None  # no HITL for a lookup


def test_data_lookup_tolerates_case_id_synonym_from_llm(app, canned_gateway_responses):
    """Regression test for the same class of bug found in Claims and
    Prior Auth: the LLM sometimes names the identifier entity 'case_id'
    instead of 'case_number'. retrieve_node must not silently fall
    through to an unfiltered query when that happens."""
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup", "confidence": 0.9,
        "entities": {"case_id": "NC300000"},  # deliberately the "wrong" key
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
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "NURSING", "roles": []}
        run_chat_turn(session_id="test-session-2", message="What is the status of case NC300000?")

    assert len(captured["ctx"]) == 1
    assert captured["ctx"][0]["source"] == "nursing_case:NC300000"


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
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "NURSING", "roles": []}
        final_state = run_chat_turn(session_id="test-session-3", message="How do we score acuity for case management?")

    assert final_state["intent"] == "policy_question"
    assert len(captured["ctx"]) > 0
    assert all(c["type"].startswith("knowledge_doc:") for c in captured["ctx"])


def test_create_record_intent_triggers_hitl_draft_and_inserts_task(app, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "create_record", "confidence": 0.85,
        "entities": {"member_id": "MBR99999", "case_type": "DISCHARGE_PLANNING"},
        "suggested_render": "none",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 80, "completion_tokens": 20, "total_cost_usd": 0.0001, "latency_ms": 45},
    }
    canned_gateway_responses["respond"] = {
        "answer_markdown": "I've drafted a nursing case for you to review.",
        "render": {"type": "text"}, "citations": [], "confidence": 0.7,
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 250, "completion_tokens": 60, "total_cost_usd": 0.0015, "latency_ms": 350},
    }
    canned_gateway_responses["hitl-draft"] = {
        "proposed_payload": {"member_id": "MBR99999", "nurse_id": "RN999", "case_type": "DISCHARGE_PLANNING",
                              "acuity_level": "MEDIUM", "status": "OPEN", "opened_date": "2026-01-01",
                              "care_plan_notes": "TEST_ROW_SAFE_TO_DELETE"},
        "missing_fields": ["case_number"],
        "rationale": "User asked to open a discharge planning case for this member.",
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 150, "completion_tokens": 40, "total_cost_usd": 0.001, "latency_ms": 300},
    }

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "NURSING", "roles": []}
        final_state = run_chat_turn(session_id="test-session-4", message="Please open a discharge planning case for member MBR99999")

    assert final_state["intent"] == "create_record"
    assert final_state["hitl"] is not None
    assert final_state["hitl"]["task_id"] > 0
    assert final_state["hitl"]["proposed_payload"]["case_type"] == "DISCHARGE_PLANNING"
    assert "case_number" in final_state["hitl"]["missing_fields"]

    from app import repository
    task = repository.get_hitl_task(final_state["hitl"]["task_id"])
    assert task["status"] == "PENDING"
    assert task["entity_type"] == "nursing_cases"


def test_hitl_draft_sends_real_schema_not_invented_fields(app, canned_gateway_responses):
    """Same class of bug caught in Claims and Prior Auth: the HITL
    draft prompt must only ever be told about real nursing_cases
    columns, never invented field names with nowhere to be saved."""
    canned_gateway_responses["intent"] = {
        "intent": "create_record", "confidence": 0.9,
        "entities": {"case_number": "NC300000"}, "suggested_render": "none",
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
            "proposed_payload": {"case_number": "NC300000", "status": "CLOSED"},
            "missing_fields": ["member_id", "nurse_id"],
            "rationale": "test",
            "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 80, "completion_tokens": 20, "total_cost_usd": 0.0003, "latency_ms": 150},
        }

    canned_gateway_responses["hitl-draft"] = fake_hitl

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "NURSING", "roles": []}
        run_chat_turn(session_id="test-session-5", message="Close case NC300000")

    from app import repository
    assert captured["entity_type"] == "nursing_cases"
    assert captured["allowed_fields"] == repository.NURSING_CASE_COLUMNS
    assert captured["required_fields"] == repository.REQUIRED_NURSING_CASE_FIELDS
    for invented in ("discharge_summary", "readmission_risk_score", "follow_up_call_notes"):
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
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "NURSING", "roles": []}
        final_state = run_chat_turn(session_id="test-session-6", message="How many cases are open vs closed?")

    assert len(final_state["usage_events"]) == 2
    total_cost = sum(e["total_cost_usd"] for e in final_state["usage_events"])
    assert round(total_cost, 5) == round(0.00005 + 0.0009, 5)
