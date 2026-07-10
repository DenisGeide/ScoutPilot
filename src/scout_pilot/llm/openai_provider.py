"""OpenAI LLM provider adapter."""

from __future__ import annotations

import json
from typing import Any

from scout_pilot.llm.config import LlmProviderConfig
from scout_pilot.llm.errors import malformed_response, provider_error_result
from scout_pilot.llm.tool_adapters import OpenAIToolSchemaAdapter
from scout_pilot.llm.types import (
    LlmErrorCode,
    LlmFinishReason,
    LlmMessageRole,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
    LlmUsage,
)


class OpenAILlmProvider:
    """OpenAI adapter behind the provider-neutral LLM interface."""

    def __init__(
        self,
        config: LlmProviderConfig,
        client: Any | None = None,
        tool_adapter: OpenAIToolSchemaAdapter | None = None,
    ) -> None:
        self._config = config
        self._client = client or _create_openai_client(config)
        self._tool_adapter = tool_adapter or OpenAIToolSchemaAdapter()

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        try:
            kwargs: dict[str, Any] = {
                "model": request.model or self._config.model,
                "messages": [
                    {"role": message.role.value, "content": message.content}
                    for message in request.messages
                ],
                "max_tokens": request.max_output_tokens or self._config.max_output_tokens,
                "timeout": request.timeout_seconds or self._config.timeout_seconds,
            }
            if request.tools:
                kwargs["tools"] = self._tool_adapter.convert_tools(request.tools)
                kwargs["tool_choice"] = "auto"
            response = await self._client.chat.completions.create(**kwargs)
            return _parse_openai_response(response)
        except Exception as exc:
            return _map_openai_exception(exc)


def _create_openai_client(config: LlmProviderConfig) -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed.") from exc
    return AsyncOpenAI(api_key=config.require_api_key(), timeout=config.timeout_seconds)


def _parse_openai_response(response: Any) -> LlmProviderResult:
    choices = getattr(response, "choices", None)
    if not choices:
        return malformed_response("OpenAI response did not contain choices.")

    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        return malformed_response("OpenAI response did not contain a message.")

    tool_calls = tuple(_parse_openai_tool_call(item) for item in (getattr(message, "tool_calls", None) or ()))
    content = getattr(message, "content", None)
    if isinstance(content, list):
        content = " ".join(str(item) for item in content)
    if not isinstance(content, str):
        content = None

    if not content and not tool_calls:
        return malformed_response("OpenAI response contained neither text nor tool calls.")

    usage = getattr(response, "usage", None)
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=_openai_finish_reason(getattr(choice, "finish_reason", None)),
            usage=LlmUsage(
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            ),
            raw_provider_name="openai",
        ),
    )


def _parse_openai_tool_call(raw_call: Any) -> LlmToolCall:
    function = getattr(raw_call, "function", None)
    if function is None:
        raise ValueError("OpenAI tool call did not contain function data.")
    raw_arguments = getattr(function, "arguments", "{}") or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI tool call arguments were malformed JSON.") from exc
    if not isinstance(arguments, dict):
        raise ValueError("OpenAI tool call arguments must decode to an object.")
    return LlmToolCall(name=str(getattr(function, "name")), arguments=arguments)


def _openai_finish_reason(reason: str | None) -> LlmFinishReason:
    if reason == "stop":
        return LlmFinishReason.STOP
    if reason == "tool_calls":
        return LlmFinishReason.TOOL_CALLS
    if reason == "length":
        return LlmFinishReason.LENGTH
    return LlmFinishReason.UNKNOWN


def _map_openai_exception(exc: Exception) -> LlmProviderResult:
    name = exc.__class__.__name__
    message = str(exc)
    if name in {"RateLimitError"}:
        return provider_error_result(LlmErrorCode.RATE_LIMIT, message, retryable=True)
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return provider_error_result(LlmErrorCode.INVALID_CREDENTIALS, message, retryable=False)
    if name in {"APITimeoutError", "TimeoutError"}:
        return provider_error_result(LlmErrorCode.TIMEOUT, message, retryable=True)
    if name in {"APIConnectionError", "InternalServerError"}:
        return provider_error_result(LlmErrorCode.PROVIDER_UNAVAILABLE, message, retryable=True)
    if name == "ValueError":
        return provider_error_result(LlmErrorCode.MALFORMED_RESPONSE, message, retryable=False)
    return provider_error_result(LlmErrorCode.UNKNOWN, message, retryable=False)
