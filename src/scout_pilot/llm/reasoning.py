"""Provider-neutral reasoning engine."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

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
from scout_pilot.models import PageObservation, ToolRequest


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
            visited_target_urls=context.visited_target_urls,
            final_answer_only=context.final_answer_only,
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
        return ReasoningResult.answer(
            _append_missing_observation_urls(content, budgeted_context.observation)
        )


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
        "visited_target_urls": list(context.visited_target_urls),
        "final_answer_only": context.final_answer_only,
        "budget": dict(context.budget),
        "context_metrics": dict(metrics.to_dict()) if metrics else None,
        "available_tool_names": [tool.name for tool in context.available_tools],
    }
    finalization_instruction = (
        " This request is final-answer-only. Do not call tools, do not ask for another "
        "observation, and do not request confirmation. Produce the best concise answer from "
        "the supplied observation and memory; state missing facts honestly."
        if context.final_answer_only
        else ""
    )
    return (
        LlmMessage(
            role=LlmMessageRole.SYSTEM,
            content=(
                "You are the reasoning component for an autonomous browser agent. "
                "Use only the compact semantic observation and the listed tools. "
                "Never assume access to raw HTML, DOM dumps, cookies, tokens, browser profiles "
                "or private files. If a tool is needed, call exactly one available tool. "
                "If more page state is required, answer with NEED_OBSERVATION: <reason>. "
                "Never request confirmation on your own. Select the concrete tool and let the "
                "deterministic Security Policy pause it before execution when required. "
                "Do not request another observation or browser.wait only because the observation "
                "is truncated when relevant visible sections or interactive elements are already "
                "available. Never repeat observation or wait on an unchanged page; answer from "
                "the available evidence or choose a different semantic tool. Track visited URLs "
                "from the explicit visited_target_urls list. Never request a URL in that list "
                "again unless the user explicitly asks to reopen it. For multi-page comparisons, "
                "use distinct target_url values from "
                "the observation and never open the same target URL twice unless the user asks. "
                "After reading a detail page, navigate to the remembered results-page URL and "
                "choose a different unvisited target. Prefer browser.back when it is available; "
                "never emulate browser history with Alt+Left or another keyboard shortcut. "
                "Call browser.fill_by_label only when the current observation contains a matching "
                "visible form field. After returning to a results page, prefer a visible unvisited "
                "link over repeating the search. A missing or ambiguous semantic target is a "
                "navigation problem to resolve or replan, not a reason to request user confirmation. "
                "When listing found "
                "pages, vacancies, products, messages or other linked items, preserve each exact "
                "target_url from the observation and print the URL directly below that item. "
                "Never invent, shorten or omit an available target URL."
                f"{finalization_instruction}"
            ),
        ),
        LlmMessage(
            role=LlmMessageRole.USER,
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


def _append_missing_observation_urls(
    answer: str,
    observation: PageObservation | None,
) -> str:
    if observation is None:
        return answer

    normalized_answer = _normalize_for_match(answer)
    seen_urls: set[str] = set()
    missing: list[tuple[str, str]] = []
    for element in observation.interactive_elements:
        name = (element.accessible_name or element.visible_text or "").strip()
        url = (element.target_url or "").strip()
        if len(name) < 4 or not _is_public_web_url(url):
            continue
        if url in answer or url in seen_urls:
            continue
        if _normalize_for_match(name) not in normalized_answer:
            continue
        seen_urls.add(url)
        missing.append((name, url))
        if len(missing) >= 10:
            break

    if not missing:
        return answer
    links = "\n".join(f"- {name}: {url}" for name, url in missing)
    return f"{answer.rstrip()}\n\nСсылки:\n{links}"


def _normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _is_public_web_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
