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


@dataclass
class DeterministicLocalDemoMockProvider:
    """Deterministic provider for the local live demo runtime path.

    The provider only reads the same compact semantic observation that a live
    provider would receive. It does not know local file names, CSS selectors, DOM
    paths, or browser internals.
    """

    requests: list[LlmProviderRequest] = field(default_factory=list)
    _search_requested: bool = False
    _ambiguity_checked: bool = False
    _detail_urls: list[str] = field(default_factory=list)
    _visited_detail_urls: set[str] = field(default_factory=set)
    _notes: list[str] = field(default_factory=list)
    _apply_requested: bool = False

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        self.requests.append(request)
        if not request.tools:
            return _local_demo_plan_response()

        payload = _request_payload(request)
        observation = payload.get("observation")
        observation_data = observation if isinstance(observation, Mapping) else {}
        self._collect_detail_urls(observation_data)
        self._collect_note(observation_data)

        if not self._search_requested and _has_click_target(
            observation_data,
            target="Show matches",
            role="button",
        ):
            self._search_requested = True
            return _tool_response(
                "browser.click_by_intent",
                {
                    "target": "Show matches",
                    "role": "button",
                    "context": "Search open roles",
                },
            )

        if self._detail_urls and not self._ambiguity_checked:
            self._ambiguity_checked = True
            return _tool_response(
                "browser.resolve_target",
                {"kind": "click", "target": "Details", "role": "link"},
            )

        next_url = self._next_unvisited_detail_url()
        if next_url is not None:
            return _tool_response("browser.navigate", {"url": next_url})

        if len(self._notes) >= 3 and not self._apply_requested:
            self._apply_requested = True
            return _tool_response(
                "browser.click_by_intent",
                {
                    "target": "Apply",
                    "role": "button",
                    "context": (
                        "Сравнение требований подготовлено по трем AI Engineer "
                        "вакансиям; следующее действие Apply требует подтверждения."
                    ),
                },
            )

        return LlmProviderResult(
            success=True,
            response=LlmProviderResponse(
                content=_local_demo_summary_ru(self._notes),
                finish_reason=LlmFinishReason.STOP,
                raw_provider_name="mock",
            ),
        )

    def _collect_detail_urls(self, observation: Mapping[str, Any]) -> None:
        for element in _interactive_elements(observation):
            target_url = element.get("target_url")
            name = _element_name(element).casefold()
            if not isinstance(target_url, str) or not target_url.strip():
                continue
            if "details" not in name:
                continue
            if target_url not in self._detail_urls:
                self._detail_urls.append(target_url)

    def _collect_note(self, observation: Mapping[str, Any]) -> None:
        url = str(observation.get("url") or "")
        if not url or url not in self._detail_urls or url in self._visited_detail_urls:
            return
        summary = _observation_text(observation)
        if not summary.strip():
            summary = str(observation.get("summary") or "")
        title = str(observation.get("title") or "Vacancy detail")
        note = f"{title}: {_compact_text(summary, 260)}"
        self._visited_detail_urls.add(url)
        if note not in self._notes:
            self._notes.append(note)

    def _next_unvisited_detail_url(self) -> str | None:
        for url in self._detail_urls:
            if url not in self._visited_detail_urls:
                return url
        return None


def _local_demo_plan_response() -> LlmProviderResult:
    content = json.dumps(
        {
            "summary": (
                "Search the local semantic site, inspect ambiguous result links, "
                "read three discovered detail pages, compare requirements, and stop before Apply."
            ),
            "steps": [
                {
                    "step_id": "show_matches",
                    "goal": "Start the local search through a semantic button.",
                    "tool_name": "browser.click_by_intent",
                    "arguments": {"target": "Show matches", "role": "button"},
                    "rationale": "Search must use semantic intent, not selectors.",
                    "requires_confirmation": False,
                    "is_uncertain": False,
                },
                {
                    "step_id": "check_ambiguous_details",
                    "goal": "Detect that several visible result links share the same Details label.",
                    "tool_name": "browser.resolve_target",
                    "arguments": {"kind": "click", "target": "Details", "role": "link"},
                    "rationale": "The agent should expose ambiguity before choosing a safer action.",
                    "requires_confirmation": False,
                    "is_uncertain": True,
                    "uncertainty_reason": "Several result links can match the same short label.",
                },
                {
                    "step_id": "read_discovered_details",
                    "goal": "Open three detail pages by URLs discovered from semantic observations.",
                    "rationale": "Navigation uses discovered target URLs, not site routes.",
                    "requires_confirmation": False,
                    "is_uncertain": False,
                },
                {
                    "step_id": "pause_before_apply",
                    "goal": "Prepare the comparison and stop at the Apply action.",
                    "tool_name": "browser.click_by_intent",
                    "arguments": {"target": "Apply", "role": "button"},
                    "rationale": "Security Policy must require confirmation before an external side effect.",
                    "requires_confirmation": True,
                    "is_uncertain": False,
                },
            ],
            "warnings": [
                "Local demo mock is deterministic and does not call a paid provider."
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


def _tool_response(name: str, arguments: Mapping[str, Any]) -> LlmProviderResult:
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(
            tool_calls=(LlmToolCall(name=name, arguments=dict(arguments)),),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            raw_provider_name="mock",
        ),
    )


def _has_click_target(
    observation: Mapping[str, Any],
    *,
    target: str,
    role: str,
) -> bool:
    target_text = target.casefold()
    role_text = role.casefold()
    return any(
        role_text in str(element.get("role") or "").casefold()
        and target_text in _element_name(element).casefold()
        for element in _interactive_elements(observation)
    )


def _interactive_elements(observation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    elements = observation.get("interactive_elements")
    if not isinstance(elements, list):
        return []
    return [element for element in elements if isinstance(element, Mapping)]


def _element_name(element: Mapping[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            element.get("accessible_name"),
            element.get("visible_text"),
        )
        if value
    )


def _observation_text(observation: Mapping[str, Any]) -> str:
    parts = [str(observation.get("summary") or "")]
    sections = observation.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, Mapping):
                parts.append(str(section.get("heading") or ""))
                parts.append(str(section.get("text") or ""))
    return " ".join(part for part in parts if part)


def _local_demo_summary_ru(notes: list[str]) -> str:
    if not notes:
        return "Сравнение требований пока не подготовлено: детали вакансий не прочитаны."
    numbered = "; ".join(f"{index}. {note}" for index, note in enumerate(notes[:3], start=1))
    return (
        "Сравнение требований подготовлено по трем локальным AI Engineer вакансиям: "
        f"{numbered}. Следующее действие — Apply, поэтому агент должен остановиться "
        "и дождаться явного подтверждения."
    )


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."
