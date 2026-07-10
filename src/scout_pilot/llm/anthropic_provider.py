"""Anthropic LLM provider adapter."""

from __future__ import annotations

from typing import Any

from scout_pilot.llm.config import LlmProviderConfig
from scout_pilot.llm.errors import malformed_response, provider_error_result
from scout_pilot.llm.tool_adapters import AnthropicToolSchemaAdapter
from scout_pilot.llm.types import (
    LlmErrorCode,
    LlmFinishReason,
    LlmMessage,
    LlmMessageRole,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
    LlmUsage,
)


class AnthropicLlmProvider:
    """Anthropic adapter behind the provider-neutral LLM interface."""

    def __init__(
        self,
        config: LlmProviderConfig,
        client: Any | None = None,
        tool_adapter: AnthropicToolSchemaAdapter | None = None,
    ) -> None:
        self._config = config
        self._client = client or _create_anthropic_client(config)
        self._tool_adapter = tool_adapter or AnthropicToolSchemaAdapter()

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        try:
            system, messages = _split_anthropic_messages(request.messages)
            kwargs: dict[str, Any] = {
                "model": request.model or self._config.model,
                "max_tokens": request.max_output_tokens or self._config.max_output_tokens,
                "messages": messages,
                "timeout": request.timeout_seconds or self._config.timeout_seconds,
            }
            if system:
                kwargs["system"] = system
            if request.tools:
                kwargs["tools"] = self._tool_adapter.convert_tools(request.tools)
            response = await self._client.messages.create(**kwargs)
            return _parse_anthropic_response(response)
        except Exception as exc:
            return _map_anthropic_exception(exc)


def _create_anthropic_client(config: LlmProviderConfig) -> Any:
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise RuntimeError("Anthropic SDK is not installed.") from exc
    return AsyncAnthropic(api_key=config.require_api_key(), timeout=config.timeout_seconds)


def _split_anthropic_messages(
    messages: tuple[LlmMessage, ...],
) -> tuple[str, list[dict[str, str]]]:
    system_parts: list[str] = []
    provider_messages: list[dict[str, str]] = []
    for message in messages:
        if message.role is LlmMessageRole.SYSTEM:
            system_parts.append(message.content)
            continue
        role = "assistant" if message.role is LlmMessageRole.ASSISTANT else "user"
        provider_messages.append({"role": role, "content": message.content})
    return "\n\n".join(system_parts), provider_messages


def _parse_anthropic_response(response: Any) -> LlmProviderResult:
    content_blocks = getattr(response, "content", None) or ()
    text_parts: list[str] = []
    tool_calls: list[LlmToolCall] = []
    for block in content_blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", None)
            if text:
                text_parts.append(str(text))
        elif block_type == "tool_use":
            raw_input = getattr(block, "input", None)
            if not isinstance(raw_input, dict):
                return malformed_response("Anthropic tool input was not an object.")
            tool_calls.append(
                LlmToolCall(
                    name=str(getattr(block, "name")),
                    arguments=raw_input,
                )
            )

    content = "\n".join(text_parts).strip() or None
    if not content and not tool_calls:
        return malformed_response("Anthropic response contained neither text nor tool calls.")

    usage = getattr(response, "usage", None)
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            finish_reason=_anthropic_finish_reason(getattr(response, "stop_reason", None)),
            usage=LlmUsage(
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                total_tokens=_sum_optional(
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                ),
            ),
            raw_provider_name="anthropic",
        ),
    )


def _anthropic_finish_reason(reason: str | None) -> LlmFinishReason:
    if reason in {"end_turn", "stop_sequence"}:
        return LlmFinishReason.STOP
    if reason == "tool_use":
        return LlmFinishReason.TOOL_CALLS
    if reason == "max_tokens":
        return LlmFinishReason.LENGTH
    return LlmFinishReason.UNKNOWN


def _sum_optional(first: int | None, second: int | None) -> int | None:
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)


def _map_anthropic_exception(exc: Exception) -> LlmProviderResult:
    name = exc.__class__.__name__
    message = str(exc)
    if name in {"RateLimitError"}:
        return provider_error_result(LlmErrorCode.RATE_LIMIT, message, retryable=True)
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return provider_error_result(LlmErrorCode.INVALID_CREDENTIALS, message, retryable=False)
    if name in {"APITimeoutError", "TimeoutError"}:
        return provider_error_result(LlmErrorCode.TIMEOUT, message, retryable=True)
    if name in {"APIConnectionError", "InternalServerError", "ServiceUnavailableError"}:
        return provider_error_result(LlmErrorCode.PROVIDER_UNAVAILABLE, message, retryable=True)
    return provider_error_result(LlmErrorCode.UNKNOWN, message, retryable=False)
