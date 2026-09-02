"""
Regression tests for a real bug caught via live manual testing (not by
any existing automated test, since every Gateway test mocks the SDK
response directly and never exercised the real prompt content): the
intent-classification tool schema and system prompt used to hardcode
'claim_number' as THE example entity key for every department. A user
asking "What is the status of PA200000?" against the Prior Auth
chatbot got back entities={"claim_number": "PA200000"} instead of
{"pa_number": "PA200000"} — because that was the only concrete example
the model was ever shown, regardless of which department was asking.
"""
from app.prompts import DEPARTMENT_ID_FIELDS, build_intent_detection_system, build_intent_tool


def test_every_real_department_has_a_mapped_id_field():
    # These are the 10 real department codes across this platform —
    # if a new department is ever added, forgetting to add it here is
    # exactly the kind of gap that reproduces the original bug.
    expected_departments = {
        "CLAIMS", "PRIORAUTH", "NURSING", "CALLCENTER", "BILLING",
        "FACPROV", "ADJUDICATION", "FINANCE", "MANAGEMENT", "MEMBERSVC",
    }
    assert expected_departments.issubset(DEPARTMENT_ID_FIELDS.keys())


def test_priorauth_tool_schema_uses_pa_number_not_claim_number():
    """The exact bug: PRIORAUTH's tool schema must name pa_number as
    its own identifier, not fall back to Claims' claim_number."""
    tool = build_intent_tool("PRIORAUTH")
    entities_description = tool["input_schema"]["properties"]["entities"]["description"]
    assert "pa_number" in entities_description
    assert "PA200000" in entities_description
    # Claims' identifier must not be presented as PRIORAUTH's own field
    assert "'claim_number' (THIS department's own record identifier" not in entities_description


def test_priorauth_system_prompt_uses_pa_number_not_claim_number():
    system = build_intent_detection_system("PRIORAUTH")
    assert "pa_number" in system
    assert "PA200000" in system
    assert "'claim_number' (e.g." not in system


def test_claims_tool_schema_still_uses_claim_number():
    """Claims itself must be unaffected — claim_number really is its
    own canonical identifier."""
    tool = build_intent_tool("CLAIMS")
    entities_description = tool["input_schema"]["properties"]["entities"]["description"]
    assert "claim_number" in entities_description
    assert "CLM100005" in entities_description


def test_each_department_gets_its_own_distinct_identifier_field():
    """Every department's tool schema must lead with ITS OWN field,
    not silently share one department's example across all of them —
    this is the actual regression test for the reported bug."""
    for dept_code, (id_field, id_example) in DEPARTMENT_ID_FIELDS.items():
        tool = build_intent_tool(dept_code)
        description = tool["input_schema"]["properties"]["entities"]["description"]
        assert id_field in description, f"{dept_code} tool schema missing its own id field '{id_field}'"
        assert id_example in description, f"{dept_code} tool schema missing its own example '{id_example}'"


def test_unknown_department_falls_back_safely():
    """A department code with no explicit mapping must not crash and
    must not silently claim 'claim_number' as if it were universal."""
    tool = build_intent_tool("SOME_FUTURE_DEPARTMENT")
    description = tool["input_schema"]["properties"]["entities"]["description"]
    assert "record_number" in description
    assert "'claim_number' (THIS department's own record identifier" not in description