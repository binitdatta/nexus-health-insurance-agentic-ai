"""
Thin wrapper around the Anthropic SDK for the Gateway's three operations.

Every call here forces a specific tool via tool_choice so the result is
always structured, parseable JSON rather than free-form prose we'd have
to regex out of a text block. The wrapper is intentionally the only
place in the app that talks to api.anthropic.com, and it always returns
a GatewayLLMResult carrying usage/cost/latency so the caller can log it
uniformly regardless of which operation was invoked.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic

from .prompts import (
    HITL_DRAFT_SYSTEM,
    HITL_TOOL,
    RESPONSE_FINALIZATION_SYSTEM,
    RESPONSE_TOOL,
    build_intent_detection_system,
    build_intent_tool,
)


class AnthropicCallError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class GatewayLLMResult:
    model: str
    operation: str
    result: dict
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    latency_ms: int
    raw_request: dict = field(default_factory=dict)
    raw_response_summary: dict = field(default_factory=dict)


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int, config) -> tuple[float, float, float]:
    pricing = config.MODEL_PRICING.get(model, config.DEFAULT_PRICING)
    in_cost = round((prompt_tokens / 1_000_000) * pricing["input_per_mtok"], 6)
    out_cost = round((completion_tokens / 1_000_000) * pricing["output_per_mtok"], 6)
    return in_cost, out_cost, round(in_cost + out_cost, 6)


class AnthropicGatewayClient:
    """
    Wraps an anthropic.Anthropic client instance. Pass a fake/mock object
    with a compatible `.messages.create(...)` method in tests instead of
    constructing a real one — nothing else in this class talks to the
    network directly.
    """

    def __init__(self, config, sdk_client: Optional[Any] = None):
        self._config = config
        self._client = sdk_client or anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY, timeout=config.ANTHROPIC_TIMEOUT_SECONDS
        )

    def _call(self, *, model: str, system: str, tool: dict, messages: list, operation: str) -> GatewayLLMResult:
        request_payload = {
            "model": model,
            "max_tokens": self._config.ANTHROPIC_MAX_TOKENS,
            "system": system,
            "messages": messages,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
        }
        started = time.monotonic()
        try:
            response = self._client.messages.create(**request_payload)
        except anthropic.APITimeoutError as exc:
            raise AnthropicCallError(f"Anthropic API timeout: {exc}", 504)
        except anthropic.APIStatusError as exc:
            raise AnthropicCallError(f"Anthropic API error ({exc.status_code}): {exc.message}", 502)
        except anthropic.APIError as exc:
            raise AnthropicCallError(f"Anthropic API error: {exc}", 502)
        latency_ms = int((time.monotonic() - started) * 1000)

        tool_use_block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
        if tool_use_block is None:
            raise AnthropicCallError("Model did not return the expected tool_use block", 502)

        prompt_tokens = response.usage.input_tokens
        completion_tokens = response.usage.output_tokens
        in_cost, out_cost, total_cost = compute_cost(model, prompt_tokens, completion_tokens, self._config)

        return GatewayLLMResult(
            model=model,
            operation=operation,
            result=tool_use_block.input,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=total_cost,
            latency_ms=latency_ms,
            raw_request=request_payload,
            raw_response_summary={
                "id": response.id,
                "stop_reason": response.stop_reason,
                "tool_use_id": tool_use_block.id,
            },
        )

    def detect_intent(self, *, message: str, department: str, conversation_history: Optional[list] = None) -> GatewayLLMResult:
        history_text = _format_history(conversation_history)
        user_content = f"Department: {department}\n{history_text}User message: {message}"
        return self._call(
            model=self._config.ANTHROPIC_FAST_MODEL,
            system=build_intent_detection_system(department),
            tool=build_intent_tool(department),
            messages=[{"role": "user", "content": user_content}],
            operation="INTENT_DETECTION",
        )

    def finalize_response(
        self, *, message: str, department: str, intent: str, retrieved_context: list,
        conversation_history: Optional[list] = None,
    ) -> GatewayLLMResult:
        context_text = _format_context(retrieved_context)
        history_text = _format_history(conversation_history)
        user_content = (
            f"Department: {department}\nClassified intent: {intent}\n{history_text}"
            f"User message: {message}\n\nretrieved_context:\n{context_text}"
        )
        return self._call(
            model=self._config.ANTHROPIC_PRIMARY_MODEL,
            system=RESPONSE_FINALIZATION_SYSTEM,
            tool=RESPONSE_TOOL,
            messages=[{"role": "user", "content": user_content}],
            operation="RESPONSE_FINALIZATION",
        )

    def draft_hitl_record(
        self, *, message: str, department: str, entity_type: str, retrieved_context: list,
        allowed_fields: list, required_fields: Optional[list] = None,
        conversation_history: Optional[list] = None,
    ) -> GatewayLLMResult:
        context_text = _format_context(retrieved_context)
        history_text = _format_history(conversation_history)
        user_content = (
            f"Department: {department}\nTarget entity_type: {entity_type}\n"
            f"allowed_fields: {', '.join(allowed_fields)}\n"
            f"required_fields: {', '.join(required_fields or [])}\n"
            f"{history_text}User message: {message}\n\nretrieved_context:\n{context_text}"
        )
        return self._call(
            model=self._config.ANTHROPIC_PRIMARY_MODEL,
            system=HITL_DRAFT_SYSTEM,
            tool=HITL_TOOL,
            messages=[{"role": "user", "content": user_content}],
            operation="HITL_DRAFT",
        )


def _format_context(retrieved_context: Optional[list]) -> str:
    if not retrieved_context:
        return "(none supplied)"
    lines = []
    for i, item in enumerate(retrieved_context):
        label = item.get("source") or item.get("id") or f"item_{i}"
        kind = item.get("type", "context")
        content = item.get("content", "")
        lines.append(f"[{label} | {kind}] {content}")
    return "\n".join(lines)


def _format_history(conversation_history: Optional[list]) -> str:
    if not conversation_history:
        return ""
    lines = [f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in conversation_history]
    return "Recent conversation:\n" + "\n".join(lines) + "\n\n"