"""Mock LLM providers for deterministic tests and local CLI smoke runs."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from scout_pilot.llm.provider import LlmProvider
from scout_pilot.llm.types import (
    LlmFinishReason,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
)


@dataclass
class MockLlmProvider:
    """Deterministic provider that returns queued results and records requests."""

    results: deque[LlmProviderResult] = field(default_factory=deque)
    requests: list[LlmProviderRequest] = field(default_factory=list)

    def __init__(self, results: list[LlmProviderResult] | None = None) -> None:
        self.results = deque(results or [])
        self.requests = []

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        self.requests.append(request)
        if not self.results:
            raise AssertionError("MockLlmProvider has no queued result.")
        return self.results.popleft()


def assert_provider_protocol(provider: LlmProvider) -> LlmProvider:
    """Return provider unchanged while helping tests type-check protocol usage."""

    return provider


@dataclass
class DeterministicBrowserMockProvider:
    """Small provider that drives the live CLI without external API calls.

    It reads the provider-neutral request shape produced by the planning and
    reasoning layers. The provider does not know website routes, selectors, or
    DOM details; it only sees the same compact semantic payload a real model
    would receive.
    """

    requests: list[LlmProviderRequest] = field(default_factory=list)
    _reasoning_calls: int = 0

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        self.requests.append(request)
        if not request.tools:
            return _mock_plan_response()
        self._reasoning_calls += 1
        if self._reasoning_calls == 1 and _has_tool(request, "browser.observe"):
            return LlmProviderResult(
                success=True,
                response=LlmProviderResponse(
                    tool_calls=(LlmToolCall(name="browser.observe", arguments={}),),
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    raw_provider_name="mock",
                ),
            )
        payload = _request_payload(request)
        return LlmProviderResult(
            success=True,
            response=LlmProviderResponse(
                content=_mock_answer_ru(payload),
                finish_reason=LlmFinishReason.STOP,
                raw_provider_name="mock",
            ),
        )


def _mock_plan_response() -> LlmProviderResult:
    content = json.dumps(
        {
            "summary": (
                "Use a live semantic observation first, then decide whether the "
                "current page already contains enough information to answer."
            ),
            "steps": [
                {
                    "step_id": "mock_observe_page",
                    "goal": "Capture a fresh semantic observation through Tool Runtime.",
                    "tool_name": "browser.observe",
                    "arguments": {},
                    "rationale": "The mock provider must inspect the current page before answering.",
                    "requires_confirmation": False,
                    "is_uncertain": False,
                }
            ],
            "warnings": [
                "Mock provider is deterministic and does not call a live LLM."
            ],
        },
        ensure_ascii=False,
    )
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(
            content=content,
            finish_reason=LlmFinishReason.STOP,
            raw_provider_name="mock",
        ),
    )


def _has_tool(request: LlmProviderRequest, name: str) -> bool:
    return any(tool.name == name for tool in request.tools)


def _request_payload(request: LlmProviderRequest) -> Mapping[str, Any]:
    if not request.messages:
        return {}
    raw = request.messages[-1].content
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def _mock_answer_ru(payload: Mapping[str, Any]) -> str:
    observation = payload.get("observation")
    if not isinstance(observation, Mapping):
        return (
            "Проверочный live-запуск завершен: страница была проверена через автономный runtime, "
            "но компактное наблюдение недоступно."
        )

    title = str(observation.get("title") or "страница без заголовка")
    url = str(observation.get("url") or "URL не определен")
    sections = observation.get("sections")
    section_count = len(sections) if isinstance(sections, list) else 0
    elements = observation.get("interactive_elements")
    element_count = len(elements) if isinstance(elements, list) else 0
    fields = observation.get("form_fields")
    field_count = len(fields) if isinstance(fields, list) else 0

    return (
        "Проверочный live-запуск завершен. Агент открыл страницу, получил безопасное "
        "семантическое наблюдение и остановился без внешних действий. "
        f"Страница: {title}. URL: {url}. "
        f"Найдено секций: {section_count}. Интерактивных элементов: {element_count}. "
        f"Полей формы: {field_count}."
    )
