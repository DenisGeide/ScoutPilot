"""Generic vacancy-search demonstration runner."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout_pilot.browser.engine import BrowserEngine
from scout_pilot.models import InteractiveElement, PageObservation, ToolRequest
from scout_pilot.observation import ObservationEngine
from scout_pilot.reporting import DemoReportRecorder
from scout_pilot.tools import DefaultToolRuntime, ToolExecutionResult, ToolExecutionStatus


ProgressCallback = Callable[[str], None]

_WORD_PATTERN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
_SIDE_EFFECT_TARGETS = (
    "apply",
    "send",
    "submit",
    "message",
    "отклик",
    "отправ",
    "сообщ",
    "подать",
)
_VACANCY_HINTS = (
    "ai",
    "ml",
    "llm",
    "python",
    "engineer",
    "developer",
    "machine",
    "learning",
    "data",
    "инженер",
    "разработчик",
    "искусственный",
    "машинн",
    "нейросет",
)
_REQUIREMENT_HINTS = (
    "require",
    "requirement",
    "experience",
    "skill",
    "python",
    "llm",
    "ml",
    "ai",
    "machine learning",
    "обязан",
    "треб",
    "опыт",
    "навык",
    "будет плюсом",
    "желательно",
)


@dataclass(frozen=True)
class VacancySearchDemoSettings:
    """User-facing settings for the generic vacancy search demo."""

    start_url: str
    query: str = "AI Engineer Python AI Developer"
    max_vacancies: int = 3
    report_path: Path = Path("reports/tmp/demo-vacancy-search-report.json")
    confirm_search_fill: bool = False
    confirm_search_submit: bool = False
    probe_security: bool = False
    wait_after_search_ms: int = 500

    def __post_init__(self) -> None:
        if not self.start_url.strip():
            raise ValueError("start_url cannot be empty")
        if not self.query.strip():
            raise ValueError("query cannot be empty")
        if self.max_vacancies <= 0:
            raise ValueError("max_vacancies must be positive")
        if self.wait_after_search_ms < 0:
            raise ValueError("wait_after_search_ms cannot be negative")


@dataclass(frozen=True)
class VacancyNote:
    """Short safe note about one discovered vacancy-like page."""

    title: str
    url: str | None
    summary: str
    requirements: tuple[str, ...]

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "requirements": list(self.requirements),
        }


@dataclass(frozen=True)
class VacancySearchDemoResult:
    """Final outcome of a vacancy search demo run."""

    success: bool
    stop_reason: str
    message_ru: str
    report_path: Path
    notes: tuple[VacancyNote, ...]
    security_pauses: tuple[Mapping[str, Any], ...]


class VacancySearchDemoRunner:
    """Run a website-neutral vacancy search through Browser and Tool Runtime layers."""

    def __init__(
        self,
        *,
        browser: BrowserEngine,
        observation_engine: ObservationEngine,
        tool_runtime: DefaultToolRuntime,
    ) -> None:
        self._browser = browser
        self._observation_engine = observation_engine
        self._tool_runtime = tool_runtime

    async def run(
        self,
        settings: VacancySearchDemoSettings,
        *,
        progress: ProgressCallback | None = None,
    ) -> VacancySearchDemoResult:
        report = DemoReportRecorder(
            demo_name="generic_vacancy_search",
            task=(
                "Find up to three suitable AI Engineer or Python AI Developer "
                "vacancy-like pages and prepare short notes without applying."
            ),
            start_url=settings.start_url,
        )
        notes: list[VacancyNote] = []
        success = False
        stop_reason = "failed"
        message_ru = "Демо завершилось с ошибкой до подготовки заметок."

        def emit(message_ru: str) -> None:
            report.record_event("progress", message_ru=message_ru)
            if progress is not None:
                progress(message_ru)

        try:
            emit("Запускаю браузер для демо.")
            await self._browser.start()

            emit("Открываю начальную страницу, переданную пользователем.")
            navigation = await self._execute(
                ToolRequest("browser.navigate", {"url": settings.start_url}),
                report=report,
                phase="open_start_url",
            )
            if not navigation.success:
                message_ru = "Не удалось открыть начальную страницу. Подробности записаны в отчет."
                return _final_result(report, settings, notes, False, "navigation_failed", message_ru)

            initial_observation = await self._observe(report, phase="initial_page")
            if _has_blocking_issue(initial_observation):
                message_ru = "Страница выглядит заблокированной или пустой. Демо остановлено честно."
                return _final_result(report, settings, notes, False, "page_not_available", message_ru)

            emit("Ищу поле поиска по семантическим признакам.")
            fill_request = ToolRequest(
                "browser.fill_by_label",
                {"label": "search", "value": settings.query},
            )
            fill_result = await self._execute(
                fill_request,
                report=report,
                phase="fill_search_query",
                auto_confirm=settings.confirm_search_fill,
                confirmation_source="confirm_search_fill_flag",
            )
            if fill_result.status is ToolExecutionStatus.PAUSED:
                message_ru = (
                    "Демо остановлено: ввод поискового запроса требует явного подтверждения. "
                    "Проверьте сообщение безопасности в отчете."
                )
                return _final_result(report, settings, notes, False, "confirmation_required", message_ru)
            if not fill_result.success:
                message_ru = "Не удалось надежно заполнить поле поиска через семантическое описание."
                return _final_result(report, settings, notes, False, "search_fill_failed", message_ru)

            emit("Запускаю поиск через семантическую кнопку поиска.")
            search_result = await self._execute(
                ToolRequest(
                    "browser.click_by_intent",
                    {"target": "search", "role": "button"},
                ),
                report=report,
                phase="run_search",
                auto_confirm=settings.confirm_search_submit,
                confirmation_source="confirm_search_submit_flag",
            )
            if search_result.status is ToolExecutionStatus.PAUSED:
                message_ru = (
                    "Демо остановлено: запуск поиска выглядит как отправка формы и требует "
                    "явного подтверждения."
                )
                return _final_result(report, settings, notes, False, "confirmation_required", message_ru)
            if not search_result.success:
                message_ru = "Не удалось надежно запустить поиск через семантическую кнопку."
                return _final_result(report, settings, notes, False, "search_click_failed", message_ru)

            if settings.wait_after_search_ms:
                await self._execute(
                    ToolRequest(
                        "browser.wait",
                        {"milliseconds": settings.wait_after_search_ms},
                    ),
                    report=report,
                    phase="wait_for_results",
                )

            results_observation = await self._observe(report, phase="search_results")
            candidates = _select_vacancy_candidates(
                results_observation,
                query=settings.query,
                limit=settings.max_vacancies,
            )
            if not candidates:
                message_ru = (
                    "Не нашлось достаточно понятных ссылок на вакансии. "
                    "Отчет содержит текущее наблюдение страницы."
                )
                return _final_result(report, settings, notes, False, "no_candidates", message_ru)

            results_url = results_observation.url
            emit(f"Нашел кандидатов для чтения: {len(candidates)}.")
            security_probe_done = False
            for index, candidate in enumerate(candidates, start=1):
                emit(f"Открываю найденную страницу {index} из {len(candidates)}.")
                opened = await self._open_candidate(candidate, report=report, phase=f"open_candidate_{index}")
                if not opened.success:
                    report.record_event(
                        "candidate_skipped",
                        index=index,
                        reason="open_failed",
                        tool_message=opened.message,
                    )
                    continue

                detail_observation = await self._observe(report, phase=f"candidate_{index}_detail")
                note = _build_vacancy_note(detail_observation, candidate)
                notes.append(note)
                report.record_note(note.to_dict())
                emit(f"Подготовил короткую заметку по странице {index}.")

                if settings.probe_security and not security_probe_done:
                    security_probe_done = True
                    await self._probe_side_effect_button(report)

                if results_url and index < len(candidates):
                    await self._execute(
                        ToolRequest("browser.navigate", {"url": results_url}),
                        report=report,
                        phase=f"return_to_results_{index}",
                    )

            if not notes:
                message_ru = "Ни одну найденную страницу не удалось прочитать достаточно надежно."
                return _final_result(report, settings, notes, False, "no_notes", message_ru)

            success = True
            stop_reason = "completed"
            message_ru = _final_summary_ru(notes)
            emit("Демо остановлено до отклика, сообщения или другой внешней отправки.")
            return _final_result(report, settings, notes, success, stop_reason, message_ru)
        except Exception as exc:
            report.record_event("error", error=str(exc), error_type=type(exc).__name__)
            message_ru = "Демо остановлено из-за непредвиденной ошибки. Подробности записаны в отчет."
            return _final_result(report, settings, notes, False, "error", message_ru)
        finally:
            await self._browser.stop()

    async def _open_candidate(
        self,
        candidate: InteractiveElement,
        *,
        report: DemoReportRecorder,
        phase: str,
    ) -> ToolExecutionResult:
        if candidate.target_url:
            return await self._execute(
                ToolRequest("browser.navigate", {"url": candidate.target_url}),
                report=report,
                phase=phase,
            )
        target = candidate.accessible_name or candidate.visible_text or "open"
        return await self._execute(
            ToolRequest(
                "browser.click_by_intent",
                {"target": target, "role": candidate.role},
            ),
            report=report,
            phase=phase,
        )

    async def _probe_side_effect_button(self, report: DemoReportRecorder) -> None:
        report.record_event(
            "decision",
            phase="probe_security_pause",
            message=(
                "Probe a likely apply/send control and stop at the deterministic "
                "security confirmation boundary."
            ),
        )
        result = await self._execute(
            ToolRequest("browser.click_by_intent", {"target": "apply", "role": "button"}),
            report=report,
            phase="probe_apply_safety",
            auto_confirm=False,
        )
        if result.status is not ToolExecutionStatus.PAUSED:
            report.record_event(
                "security_probe_result",
                status=result.status.value,
                success=result.success,
                message=result.message,
            )

    async def _observe(
        self,
        report: DemoReportRecorder,
        *,
        phase: str,
    ) -> PageObservation:
        observation = await self._observation_engine.observe()
        report.record_event(
            "observation",
            phase=phase,
            observation=_observation_to_report(observation),
        )
        return observation

    async def _execute(
        self,
        request: ToolRequest,
        *,
        report: DemoReportRecorder,
        phase: str,
        auto_confirm: bool = False,
        confirmation_source: str | None = None,
    ) -> ToolExecutionResult:
        report.record_event(
            "selected_tool",
            phase=phase,
            tool_name=request.name,
            arguments=_redact_tool_arguments(request.arguments),
        )
        result = await self._tool_runtime.execute(request)
        report.record_event(
            "tool_result",
            phase=phase,
            tool_name=request.name,
            status=result.status.value,
            success=result.success,
            message=result.message,
            error_code=result.error_code,
            retryable=result.retryable,
            data=_tool_data_to_report(result.data),
        )
        if result.status is not ToolExecutionStatus.PAUSED:
            return result

        confirmation = _confirmation_from_result(result)
        report.record_security_pause(
            phase=phase,
            tool_name=request.name,
            message_ru=str(confirmation.get("message_ru") or result.message),
            risk=_nested_value(result.data, "security", "risk"),
            confirmation_id=confirmation.get("confirmation_id"),
            action=confirmation.get("action"),
            expected_consequence=confirmation.get("expected_consequence"),
        )
        if not auto_confirm:
            return result

        confirmation_id = str(confirmation.get("confirmation_id") or "")
        confirmed = bool(confirmation_id) and self._tool_runtime.confirm_pending_action(
            confirmation_id
        )
        report.record_event(
            "explicit_confirmation",
            phase=phase,
            confirmation_id=confirmation_id,
            confirmed=confirmed,
            source=confirmation_source or "demo_settings",
        )
        if not confirmed:
            return result

        confirmed_result = await self._tool_runtime.execute(request)
        report.record_event(
            "tool_result_after_confirmation",
            phase=phase,
            tool_name=request.name,
            status=confirmed_result.status.value,
            success=confirmed_result.success,
            message=confirmed_result.message,
            error_code=confirmed_result.error_code,
            retryable=confirmed_result.retryable,
            data=_tool_data_to_report(confirmed_result.data),
        )
        return confirmed_result


def _final_result(
    report: DemoReportRecorder,
    settings: VacancySearchDemoSettings,
    notes: list[VacancyNote],
    success: bool,
    stop_reason: str,
    message_ru: str,
) -> VacancySearchDemoResult:
    report.set_final(success=success, stop_reason=stop_reason, summary_ru=message_ru)
    report_path = report.write(settings.report_path)
    return VacancySearchDemoResult(
        success=success,
        stop_reason=stop_reason,
        message_ru=message_ru,
        report_path=report_path,
        notes=tuple(notes),
        security_pauses=report.security_pauses,
    )


def _select_vacancy_candidates(
    observation: PageObservation,
    *,
    query: str,
    limit: int,
) -> tuple[InteractiveElement, ...]:
    query_tokens = _tokens(query)
    scored: list[tuple[int, int, InteractiveElement]] = []
    seen: set[str] = set()
    for index, element in enumerate(observation.interactive_elements):
        if element.state.disabled:
            continue
        if element.role not in {"link", "button"} and element.target_url is None:
            continue
        text = _element_text(element)
        if not text or _looks_like_side_effect(text):
            continue
        dedupe_key = _candidate_dedupe_key(element)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        tokens = _tokens(text)
        score = 0
        score += len(tokens & query_tokens) * 20
        if any(hint in text.casefold() for hint in _VACANCY_HINTS):
            score += 25
        if element.role == "link":
            score += 12
        if element.target_url:
            score += 10
        if score > 0:
            scored.append((score, -index, element))

    ordered = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(element for _score, _index, element in ordered[:limit])


def _build_vacancy_note(
    observation: PageObservation,
    candidate: InteractiveElement,
) -> VacancyNote:
    title = (
        observation.title
        or candidate.accessible_name
        or candidate.visible_text
        or "Найденная страница"
    )
    visible_text = " ".join(section.text for section in observation.sections if section.text)
    summary = _truncate_text(visible_text or observation.summary, 520)
    requirements = _extract_requirements(visible_text)
    return VacancyNote(
        title=_truncate_text(title, 180),
        url=observation.url or candidate.target_url,
        summary=summary,
        requirements=requirements,
    )


def _extract_requirements(text: str) -> tuple[str, ...]:
    chunks = [
        _truncate_text(chunk.strip(), 220)
        for chunk in _SENTENCE_SPLIT_PATTERN.split(text)
        if chunk.strip()
    ]
    requirements: list[str] = []
    for chunk in chunks:
        lowered = chunk.casefold()
        if any(hint in lowered for hint in _REQUIREMENT_HINTS):
            requirements.append(chunk)
        if len(requirements) >= 5:
            break
    if requirements:
        return tuple(requirements)
    return tuple(chunks[:3])


def _final_summary_ru(notes: list[VacancyNote]) -> str:
    titles = "; ".join(note.title for note in notes)
    return (
        f"Демо прочитало {len(notes)} найденные страницы и подготовило короткие заметки: "
        f"{titles}. Отклики, сообщения и отправка форм не выполнялись."
    )


def _observation_to_report(observation: PageObservation) -> Mapping[str, Any]:
    return {
        "url": observation.url,
        "title": observation.title,
        "summary": observation.summary,
        "issues": [
            {
                "code": issue.code.value,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in observation.issues
        ],
        "sections": [
            {
                "id": section.section_id,
                "role": section.role,
                "heading": section.heading,
                "text": _truncate_text(section.text, 500),
            }
            for section in observation.sections[:8]
        ],
        "interactive_elements": [
            {
                "id": element.element_id,
                "role": element.role,
                "accessible_name": element.accessible_name,
                "visible_text": element.visible_text,
                "target_url": element.target_url,
                "input_type": element.input_type,
            }
            for element in observation.interactive_elements[:16]
        ],
        "form_fields": [
            {
                "id": field.field_id,
                "role": field.role,
                "input_type": field.input_type,
                "label": field.label,
                "placeholder": field.placeholder,
                "value_state": field.value_state,
            }
            for field in observation.form_fields[:8]
        ],
    }


def _tool_data_to_report(data: Mapping[str, Any]) -> Mapping[str, Any]:
    allowed_keys = {
        "action",
        "url",
        "title",
        "resolution",
        "transition",
        "recovered_from_stale",
        "security",
        "confirmation",
    }
    return {
        key: _sanitize_report_value(value)
        for key, value in data.items()
        if key in allowed_keys
    }


def _sanitize_report_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_report_value(item)
            for key, item in value.items()
            if str(key).casefold() not in {"request_signature"}
        }
    if isinstance(value, tuple | list):
        return [_sanitize_report_value(item) for item in value]
    if isinstance(value, str):
        return _truncate_text(value, 800)
    return value


def _redact_tool_arguments(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in arguments.items():
        if key.casefold() in {"value", "password", "token", "secret"}:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


def _confirmation_from_result(result: ToolExecutionResult) -> Mapping[str, Any]:
    raw = result.data.get("confirmation")
    return raw if isinstance(raw, Mapping) else {}


def _nested_value(data: Mapping[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _has_blocking_issue(observation: PageObservation) -> bool:
    codes = {issue.code.value for issue in observation.issues}
    return "blocked_page" in codes or (
        "empty_page" in codes
        and not observation.interactive_elements
        and not observation.sections
    )


def _element_text(element: InteractiveElement) -> str:
    return " ".join(
        part
        for part in (
            element.role,
            element.accessible_name,
            element.visible_text,
            element.target_url,
            element.input_type,
        )
        if part
    )


def _candidate_dedupe_key(element: InteractiveElement) -> str:
    return (
        element.target_url
        or element.accessible_name
        or element.visible_text
        or element.element_id
    ).casefold()


def _looks_like_side_effect(text: str) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in _SIDE_EFFECT_TARGETS)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _WORD_PATTERN.finditer(text))


def _truncate_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."
