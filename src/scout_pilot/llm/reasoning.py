"""Provider-neutral reasoning engine."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from scout_pilot.context import (
    ContextBudgetSettings,
    ContextCompressionMetrics,
    DeterministicContextBudgeter,
)
from scout_pilot.llm.provider import LlmProvider
from scout_pilot.llm.types import (
    LlmErrorCode,
    LlmFinishReason,
    LlmMessage,
    LlmMessageRole,
    LlmProviderError,
    LlmProviderRequest,
    ReasoningContext,
    ReasoningResult,
)
from scout_pilot.models import ToolRequest


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReasoningSettings:
    """Reasoning request limits."""

    model: str | None = None
    max_output_tokens: int = 1200
    timeout_seconds: float = 30.0
    max_input_tokens: int = 8000
    max_observation_tokens: int = 3500
    max_memory_tokens: int = 1400


class ReasoningEngine:
    """Ask a provider for the next provider-neutral reasoning decision."""

    def __init__(
        self,
        provider: LlmProvider,
        settings: ReasoningSettings | None = None,
        context_budgeter: DeterministicContextBudgeter | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings or ReasoningSettings()
        self._context_budgeter = context_budgeter or DeterministicContextBudgeter(
            ContextBudgetSettings(
                max_input_tokens=self._settings.max_input_tokens,
                reserved_output_tokens=self._settings.max_output_tokens,
                max_observation_tokens=self._settings.max_observation_tokens,
                max_memory_tokens=self._settings.max_memory_tokens,
            )
        )
        self.last_context_metrics: ContextCompressionMetrics | None = None

    async def reason(self, context: ReasoningContext) -> ReasoningResult:
        budgeted = self._context_budgeter.assemble(
            user_task=context.user_task,
            observation=context.observation,
            memory_summaries=context.memory_summaries,
            budget=context.budget,
            max_input_tokens=self._settings.max_input_tokens,
            reserved_output_tokens=self._settings.max_output_tokens,
        )
        self.last_context_metrics = budgeted.metrics
        budgeted_context = ReasoningContext(
            user_task=context.user_task,
            observation=budgeted.observation,
            memory_summaries=budgeted.memory_summaries,
            available_tools=context.available_tools,
            security_constraints=context.security_constraints,
            confirmation_constraints=context.confirmation_constraints,
            budget=budgeted.budget,
        )
        request = LlmProviderRequest(
            messages=_build_messages(budgeted_context, budgeted.metrics),
            tools=context.available_tools,
            model=self._settings.model,
            max_output_tokens=self._settings.max_output_tokens,
            timeout_seconds=self._settings.timeout_seconds,
        )
        try:
            result = await self._provider.complete(request)
        except TimeoutError as exc:
            return ReasoningResult.failure(
                "LLM provider timed out before returning a structured result.",
                LlmProviderError(
                    code=LlmErrorCode.TIMEOUT,
                    message=str(exc),
                    retryable=True,
                ),
            )
        except Exception as exc:
            logger.info(
                "llm_provider_exception",
                extra={
                    "event": "llm_provider_exception",
                    "error_type": type(exc).__name__,
                },
            )
            return ReasoningResult.failure(
                "LLM provider raised an unexpected error before returning a structured result.",
                LlmProviderError(
                    code=LlmErrorCode.UNKNOWN,
                    message=str(exc),
                    retryable=False,
                ),
            )
        if not result.success:
            return ReasoningResult.failure(
                result.error.message if result.error else "LLM provider failed.",
                result.error,
            )
        if result.response is None:
            return ReasoningResult.failure("LLM provider returned no response.")
        if result.response.finish_reason is LlmFinishReason.LENGTH:
            return ReasoningResult.failure(
                "LLM provider stopped because the output token limit was reached.",
                LlmProviderError(
                    code=LlmErrorCode.MALFORMED_RESPONSE,
                    message="Provider response ended at max output tokens.",
                    retryable=True,
                ),
            )

        if result.response.tool_calls:
            tool_call = result.response.tool_calls[0]
            if not tool_call.name.strip():
                return ReasoningResult.failure("Model selected an empty tool name.")
            available_names = {schema.name for schema in context.available_tools}
            if available_names and tool_call.name not in available_names:
                return ReasoningResult.failure(f"Model selected unknown tool: {tool_call.name}")
            if not isinstance(tool_call.arguments, Mapping):
                return ReasoningResult.failure("Model tool arguments were not an object.")
            return ReasoningResult.tool_selected(
                ToolRequest(name=tool_call.name, arguments=dict(tool_call.arguments))
            )

        content = (result.response.content or "").strip()
        if not content:
            return ReasoningResult.failure("LLM provider returned an empty response.")
        lowered = content.casefold()
        if lowered.startswith("need_observation:"):
            return ReasoningResult.needs_observation(content.split(":", 1)[1].strip())
        if lowered.startswith("need_confirmation:"):
            return ReasoningResult.needs_confirmation(content.split(":", 1)[1].strip())
        return ReasoningResult.answer(content)


def _build_messages(
    context: ReasoningContext,
    metrics: ContextCompressionMetrics | None = None,
) -> tuple[LlmMessage, ...]:
    payload = {
        "user_task": context.user_task,
        "observation": context.observation.to_llm_context() if context.observation else None,
        "memory_summaries": list(context.memory_summaries),
        "security_constraints": list(context.security_constraints),
        "confirmation_constraints": list(context.confirmation_constraints),
        "budget": dict(context.budget),
        "context_metrics": dict(metrics.to_dict()) if metrics else None,
        "available_tool_names": [tool.name for tool in context.available_tools],
    }
    return (
        LlmMessage(
            role=LlmMessageRole.SYSTEM,
            content=(
                "You are the reasoning component for an autonomous browser agent. "
                "Use only the compact semantic observation and the listed tools. "
                "Never assume access to raw HTML, DOM dumps, cookies, tokens, browser profiles "
                "or private files. If a tool is needed, call exactly one available tool. "
                "If more page state is required, answer with NEED_OBSERVATION: <reason>. "
                "If user confirmation is required, answer with NEED_CONFIRMATION: <reason>."
            ),
        ),
        LlmMessage(
            role=LlmMessageRole.USER,
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )
