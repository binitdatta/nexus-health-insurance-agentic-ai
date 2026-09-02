from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    session_id: str
    message: str
    conversation_history: list
    requested_by_user_id: int

    intent: str
    confidence: float
    entities: dict
    suggested_render: str

    retrieved_context: list

    answer_markdown: str
    render: dict
    citations: list
    response_confidence: float

    hitl: dict
    usage_events: list[dict[str, Any]]
