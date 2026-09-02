from langgraph.graph import END, START, StateGraph

from .nodes import classify_intent_node, hitl_draft_node, retrieve_node, synthesize_node
from .state import ChatState

_HITL_INTENTS = {"create_record", "update_record"}

_compiled_graph = None


def _route_after_synthesize(state: ChatState) -> str:
    return "hitl_draft" if state.get("intent") in _HITL_INTENTS else END


def build_graph():
    graph = StateGraph(ChatState)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("hitl_draft", hitl_draft_node)

    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "retrieve")
    graph.add_edge("retrieve", "synthesize")
    graph.add_conditional_edges("synthesize", _route_after_synthesize, {"hitl_draft": "hitl_draft", END: END})
    graph.add_edge("hitl_draft", END)
    return graph.compile()


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run_chat_turn(*, session_id: str, message: str, conversation_history: list = None,
                   requested_by_user_id: int = None) -> ChatState:
    initial_state: ChatState = {
        "session_id": session_id,
        "message": message,
        "conversation_history": conversation_history or [],
        "requested_by_user_id": requested_by_user_id,
        "usage_events": [],
    }
    return get_graph().invoke(initial_state)
