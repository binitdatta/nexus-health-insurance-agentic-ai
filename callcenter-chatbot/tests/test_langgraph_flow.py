from app.langgraph_flow.graph import run_chat_turn


def test_data_lookup_retrieves_real_call_and_renders_table(app, logged_in_client, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup",
        "confidence": 0.95,
        "entities": {"call_reference": "CALL400000"},
        "suggested_render": "table",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 100, "completion_tokens": 20, "total_cost_usd": 0.0001, "latency_ms": 50},
    }

    captured_context = {}

    def fake_respond(payload):
        captured_context["retrieved_context"] = payload["retrieved_context"]
        return {
            "answer_markdown": "CALL400000 details below.",
            "render": {"type": "table", "spec": {"columns": ["call_reference"], "rows": [["CALL400000"]]}},
            "citations": ["call:CALL400000"],
            "confidence": 0.9,
            "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 300, "completion_tokens": 80, "total_cost_usd": 0.002, "latency_ms": 400},
        }

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        session["chat_session_id"] = "test-session-1"

        final_state = run_chat_turn(session_id="test-session-1", message="What happened on call CALL400000?")

    assert final_state["intent"] == "data_lookup"
    # Retrieval must have actually queried the real seeded call_center_logs table
    assert len(captured_context["retrieved_context"]) == 1
    assert captured_context["retrieved_context"][0]["source"] == "call:CALL400000"
    assert "call_reference=CALL400000" in captured_context["retrieved_context"][0]["content"]
    assert "MBR51180" in captured_context["retrieved_context"][0]["content"]  # real seeded member for CALL400000
    assert final_state["render"]["type"] == "table"
    assert final_state.get("hitl") is None  # no HITL for a lookup


def test_data_lookup_tolerates_call_id_synonym_from_llm(app, canned_gateway_responses):
    """Regression test for the same class of bug found across every
    other department chatbot: the LLM sometimes names the identifier
    entity 'call_id' instead of 'call_reference'. retrieve_node must
    not silently fall through to an unfiltered query when that
    happens."""
    canned_gateway_responses["intent"] = {
        "intent": "data_lookup", "confidence": 0.9,
        "entities": {"call_id": "CALL400000"},  # deliberately the "wrong" key
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
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        run_chat_turn(session_id="test-session-2", message="What happened on call CALL400000?")

    assert len(captured["ctx"]) == 1
    assert captured["ctx"][0]["source"] == "call:CALL400000"


def test_data_lookup_datetime_range_includes_full_last_day(app, canned_gateway_responses):
    """Real bug class this chatbot is specifically built to avoid:
    call_datetime is a full DATETIME, not just DATE like every other
    department chatbot's date filters. A naive `<= date_to` comparison
    would silently exclude a call after midnight on the last day of
    the range — confirmed live against the real DB during this
    chatbot's build (see README). This inserts a real boundary-case
    row and proves retrieve_node's date range actually includes it."""
    from app import extensions, repository
    dept_id = repository.get_dept_id("CALLCENTER")
    conn = extensions.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO call_center_logs (dept_id, call_reference, member_id, agent_id, call_datetime, "
                "call_type, resolution_status, call_notes) VALUES (%s, 'CALLTESTBOUNDARY', 'MBRTESTBD', "
                "'AGTTESTBD', '2026-07-31 23:45:00', 'BENEFITS', 'RESOLVED', 'TEST_ROW_SAFE_TO_DELETE')",
                (dept_id,),
            )
    finally:
        conn.close()

    canned_gateway_responses["intent"] = {
        "intent": "data_lookup", "confidence": 0.9,
        "entities": {"date_from": "2026-07-01", "date_to": "2026-07-31"},
        "suggested_render": "table",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 90, "completion_tokens": 15, "total_cost_usd": 0.0001, "latency_ms": 40},
    }
    captured = {}

    def fake_respond(payload):
        captured["ctx"] = payload["retrieved_context"]
        return {"answer_markdown": "Here are July calls.", "render": {"type": "table"}, "citations": [], "confidence": 0.8,
                "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 200, "completion_tokens": 50, "total_cost_usd": 0.001, "latency_ms": 300}}

    canned_gateway_responses["respond"] = fake_respond

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        run_chat_turn(session_id="test-session-3", message="Show me July 2026 calls")

    matching = [c for c in captured["ctx"] if c["source"] == "call:CALLTESTBOUNDARY"]
    assert len(matching) == 1, "23:45 call on the last day of the range was excluded — date boundary regressed"


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
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        final_state = run_chat_turn(session_id="test-session-4", message="How do we verify a caller's identity?")

    assert final_state["intent"] == "policy_question"
    assert len(captured["ctx"]) > 0
    assert all(c["type"].startswith("knowledge_doc:") for c in captured["ctx"])


def test_create_record_intent_triggers_hitl_draft_and_inserts_task(app, canned_gateway_responses):
    canned_gateway_responses["intent"] = {
        "intent": "create_record", "confidence": 0.85,
        "entities": {"member_id": "MBR99999", "call_type": "BENEFITS"},
        "suggested_render": "none",
        "usage": {"model": "claude-haiku-4-5-20251001", "prompt_tokens": 80, "completion_tokens": 20, "total_cost_usd": 0.0001, "latency_ms": 45},
    }
    canned_gateway_responses["respond"] = {
        "answer_markdown": "I've logged the call for you to review.",
        "render": {"type": "text"}, "citations": [], "confidence": 0.7,
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 250, "completion_tokens": 60, "total_cost_usd": 0.0015, "latency_ms": 350},
    }
    canned_gateway_responses["hitl-draft"] = {
        "proposed_payload": {"member_id": "MBR99999", "call_type": "BENEFITS", "resolution_status": "RESOLVED",
                              "call_notes": "TEST_ROW_SAFE_TO_DELETE"},
        "missing_fields": ["call_reference", "agent_id", "call_datetime"],
        "rationale": "User asked to log a new benefits call for this member.",
        "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 150, "completion_tokens": 40, "total_cost_usd": 0.001, "latency_ms": 300},
    }

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        final_state = run_chat_turn(session_id="test-session-5", message="Log a benefits call for member MBR99999")

    assert final_state["intent"] == "create_record"
    assert final_state["hitl"] is not None
    assert final_state["hitl"]["task_id"] > 0
    assert final_state["hitl"]["proposed_payload"]["call_type"] == "BENEFITS"
    assert "call_reference" in final_state["hitl"]["missing_fields"]

    from app import repository
    task = repository.get_hitl_task(final_state["hitl"]["task_id"])
    assert task["status"] == "PENDING"
    assert task["entity_type"] == "call_center_logs"


def test_hitl_draft_sends_real_schema_not_invented_fields(app, canned_gateway_responses):
    """Same class of bug caught across every other department chatbot:
    the HITL draft prompt must only ever be told about real
    call_center_logs columns, never invented field names with nowhere
    to be saved."""
    canned_gateway_responses["intent"] = {
        "intent": "create_record", "confidence": 0.9,
        "entities": {"call_reference": "CALL400000"}, "suggested_render": "none",
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
            "proposed_payload": {"call_reference": "CALL400000", "resolution_status": "RESOLVED"},
            "missing_fields": [],
            "rationale": "test",
            "usage": {"model": "claude-sonnet-4-6", "prompt_tokens": 80, "completion_tokens": 20, "total_cost_usd": 0.0003, "latency_ms": 150},
        }

    canned_gateway_responses["hitl-draft"] = fake_hitl

    with app.test_request_context("/api/chat"):
        from flask import session
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        run_chat_turn(session_id="test-session-6", message="Mark call CALL400000 as resolved")

    from app import repository
    assert captured["entity_type"] == "call_center_logs"
    assert captured["allowed_fields"] == repository.CALL_COLUMNS
    assert captured["required_fields"] == repository.REQUIRED_CALL_FIELDS
    for invented in ("sentiment_score", "callback_requested", "supervisor_flag"):
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
        session["user"] = {"sub": "dev-user-sub", "username": "dev.tester", "email": "e@x.com", "department": "CALLCENTER", "roles": []}
        final_state = run_chat_turn(session_id="test-session-7", message="What is our average CSAT by call type?")

    assert len(final_state["usage_events"]) == 2
    total_cost = sum(e["total_cost_usd"] for e in final_state["usage_events"])
    assert round(total_cost, 5) == round(0.00005 + 0.0009, 5)
