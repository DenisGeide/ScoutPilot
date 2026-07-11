"""Human-readable safe summaries for report and replay artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_RAW_HTML_PATTERN = re.compile(
    r"(?is)<!doctype\s+html|<html[\s>]|<body[\s>]|<script[\s>]|</[a-z][a-z0-9:-]*>"
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|token|secret|cookie|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:\\(?:users|documents and settings)\\[^\s,;\"']+|/(?:home|users)/[^\s,;\"']+)"
)
_SENSITIVE_KEY_HINTS = (
    "api_key",
    "authorization",
    "browser_profile",
    "cookie",
    "password",
    "private_file",
    "profile_path",
    "raw_html",
    "resume",
    "secret",
    "session",
    "storage_state",
    "token",
    "user_data_dir",
)
_SAFE_REDACTED_VALUES = {
    "[REDACTED]",
    "[REDACTED_PATH]",
    "[REDACTED_RAW_HTML]",
    "Bearer [REDACTED]",
}
_OBSERVATION_EVENTS = {"observation", "observation_captured", "post_action_observation_captured"}
_TOOL_EVENTS = {"selected_tool", "tool_selected"}
_CONTEXT_EVENTS = {"context_budget", "context_budget_applied"}
_BLOCKER_EVENTS = {"page_blocker", "page_blocker_detected"}


@dataclass(frozen=True)
class ReplaySafetyFinding:
    """A safety issue found before printing an artifact summary."""

    severity: str
    message_ru: str


@dataclass(frozen=True)
class ReplaySummary:
    """Safe printable summary for one report or replay JSON artifact."""

    artifact_kind: str
    safe_to_print: bool
    findings: tuple[ReplaySafetyFinding, ...]
    lines: tuple[str, ...]


class ReplaySummaryError(ValueError):
    """Raised when an artifact cannot be loaded as a supported JSON object."""


def summarize_replay_file(path: Path) -> ReplaySummary:
    """Load a report/replay JSON file and return a safe Russian summary."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReplaySummaryError(f"Не удалось прочитать файл: {path}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplaySummaryError("Файл не является корректным JSON.") from exc

    if not isinstance(payload, Mapping):
        raise ReplaySummaryError("Файл должен содержать JSON-объект report/replay.")

    return summarize_replay_payload(payload)


def summarize_replay_payload(payload: Mapping[str, Any]) -> ReplaySummary:
    """Return a safe summary from an already loaded artifact payload."""

    artifact_kind = str(payload.get("artifact_kind") or "unknown")
    findings = tuple(_scan_safety(payload))
    blocking_findings = [finding for finding in findings if finding.severity == "error"]
    if blocking_findings:
        return ReplaySummary(
            artifact_kind=artifact_kind,
            safe_to_print=False,
            findings=findings,
            lines=_unsafe_lines(artifact_kind, findings),
        )

    events = _events(payload)
    summary = _mapping(payload.get("summary"))
    lines = [
        "Сводка отчета/replay",
        f"Тип артефакта: {artifact_kind}",
        f"Задача: {_value_text(payload.get('task'), 'не указана')}",
        f"Итог: {_final_status(payload, events)}",
        *_pages_lines(payload, events),
        f"Наблюдения: {_observation_count(payload, events, summary)}",
        *_tool_lines(payload, events, summary),
        *_security_lines(payload, events, summary),
        *_context_lines(events, summary),
        *_notes_lines(payload, events),
        *_blocker_lines(payload, events, summary),
    ]
    if findings:
        lines.append("Предупреждения безопасности:")
        lines.extend(f"- {finding.message_ru}" for finding in findings)
    return ReplaySummary(
        artifact_kind=artifact_kind,
        safe_to_print=True,
        findings=findings,
        lines=tuple(lines),
    )


def _unsafe_lines(
    artifact_kind: str,
    findings: Sequence[ReplaySafetyFinding],
) -> tuple[str, ...]:
    lines = [
        "Файл report/replay не показан как обычная сводка.",
        f"Тип артефакта: {artifact_kind}",
        "Причина: внутри есть небезопасные или неочищенные данные.",
        "Что сделать: пересоздайте report/replay через Scout Pilot или удалите приватные данные вручную.",
        "Найденные проблемы:",
    ]
    lines.extend(f"- {finding.message_ru}" for finding in findings)
    return tuple(lines)


def _scan_safety(value: Any) -> Iterable[ReplaySafetyFinding]:
    yield from _scan_value(value)


def _scan_value(value: Any, *, key_hint: str = "") -> Iterable[ReplaySafetyFinding]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text) and not _is_safe_sensitive_value(item):
                yield ReplaySafetyFinding(
                    "error",
                    "Найдено неочищенное чувствительное поле.",
                )
            yield from _scan_value(item, key_hint=key_text)
        return

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            yield from _scan_value(item, key_hint=key_hint)
        return

    if not isinstance(value, str):
        return

    if _is_safe_redacted_text(value):
        return
    if key_hint in {"artifact_kind", "demo_name", "mode", "status"}:
        return
    if _RAW_HTML_PATTERN.search(value):
        yield ReplaySafetyFinding("error", "Найдено содержимое, похожее на raw HTML.")
    if _SECRET_VALUE_PATTERN.search(value) or _BEARER_PATTERN.search(value):
        yield ReplaySafetyFinding("error", "Найдено неочищенное чувствительное значение.")
    if _PRIVATE_PATH_PATTERN.search(value):
        yield ReplaySafetyFinding("error", "Найден локальный приватный путь.")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized.endswith("_tokens") or normalized.endswith("_token_count"):
        return False
    if normalized in {
        "raw_html_included",
        "sensitive_values_redacted",
        "private_browser_artifacts_included",
    }:
        return False
    return any(hint in normalized for hint in _SENSITIVE_KEY_HINTS)


def _is_safe_sensitive_value(value: Any) -> bool:
    if isinstance(value, str):
        return _is_safe_redacted_text(value)
    if isinstance(value, bool):
        return value is False
    if value is None:
        return True
    if isinstance(value, Mapping):
        return all(_is_safe_sensitive_value(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return all(_is_safe_sensitive_value(item) for item in value)
    return False


def _is_safe_redacted_text(value: str) -> bool:
    return value.strip() in _SAFE_REDACTED_VALUES


def _events(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_events = payload.get("events")
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, str | bytes | bytearray):
        return ()
    return tuple(item for item in raw_events if isinstance(item, Mapping))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _event_name(event: Mapping[str, Any]) -> str:
    return str(event.get("kind") or event.get("name") or "")


def _event_details(event: Mapping[str, Any]) -> Mapping[str, Any]:
    details = event.get("details")
    return details if isinstance(details, Mapping) else event


def _final_status(payload: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    success = payload.get("success")
    stop_reason = payload.get("stop_reason")
    final = _mapping(payload.get("final"))
    if not isinstance(success, bool):
        final_success = final.get("success")
        success = final_success if isinstance(final_success, bool) else None
    if not stop_reason:
        failure = final.get("failure_ru")
        stop_reason = failure if isinstance(failure, str) and failure else None
    if not isinstance(success, bool):
        for event in reversed(events):
            details = _event_details(event)
            event_success = details.get("success")
            if isinstance(event_success, bool):
                success = event_success
                break
    status = "успешно" if success is True else "остановлено или ошибка" if success is False else "не указан"
    if stop_reason:
        return f"{status}; причина: {_short_text(str(stop_reason), 220)}"
    return status


def _pages_lines(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    pages: list[tuple[str, str, str]] = []
    for page in _sequence_of_mappings(payload.get("pages_read")):
        title = _value_text(page.get("title"), "без заголовка")
        url = _value_text(page.get("url"), "")
        phase = _value_text(page.get("phase"), "страница")
        pages.append((phase, title, url))
    for event in events:
        name = _event_name(event)
        details = _event_details(event)
        if name not in _OBSERVATION_EVENTS:
            continue
        nested = _mapping(details.get("observation"))
        title = nested.get("title") or details.get("title")
        url = nested.get("url") or details.get("url")
        if title or url:
            pages.append((name, _value_text(title, "без заголовка"), _value_text(url, "")))
    start_url = payload.get("start_url")
    if start_url:
        pages.insert(0, ("start", "стартовая страница", str(start_url)))
    unique = _unique_pages(pages)
    lines = [f"Страницы: {len(unique)}"]
    if not unique:
        lines.append("- не записаны")
        return tuple(lines)
    for phase, title, url in unique[:10]:
        suffix = f" — {url}" if url else ""
        lines.append(f"- {phase}: {_short_text(title, 120)}{suffix}")
    if len(unique) > 10:
        lines.append(f"- еще {len(unique) - 10} страниц не показано в краткой сводке")
    return tuple(lines)


def _unique_pages(pages: Sequence[tuple[str, str, str]]) -> tuple[tuple[str, str, str], ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for phase, title, url in pages:
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        unique.append((phase, title, url))
    return tuple(unique)


def _observation_count(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> int:
    explicit = summary.get("observation_count")
    if isinstance(explicit, int):
        return explicit
    return sum(1 for event in events if _event_name(event) in _OBSERVATION_EVENTS)


def _tool_lines(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> tuple[str, ...]:
    tools: list[str] = []
    for tool in _string_sequence(summary.get("selected_tools")):
        tools.append(tool)
    for event in events:
        if _event_name(event) not in _TOOL_EVENTS:
            continue
        details = _event_details(event)
        tool = details.get("tool_name") or details.get("selected_tool")
        if tool:
            tools.append(str(tool))
    unique = list(dict.fromkeys(tools))
    count = summary.get("tool_decision_count")
    if not isinstance(count, int):
        count = sum(1 for event in events if _event_name(event) in _TOOL_EVENTS)
    if not unique:
        return (f"Вызовы инструментов: {count}; инструменты не записаны",)
    return (f"Вызовы инструментов: {count}; {', '.join(unique[:12])}",)


def _security_lines(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> tuple[str, ...]:
    pauses: list[Mapping[str, Any]] = list(_sequence_of_mappings(payload.get("security_pauses")))
    for event in events:
        name = _event_name(event)
        details = _event_details(event)
        if name == "security_pause" or name == "confirmation_required":
            pauses.append(details)
            continue
        decision = details.get("security_decision")
        if isinstance(decision, Mapping):
            status = str(decision.get("status") or decision.get("reason") or "")
            if "confirmation" in status.casefold() or details.get("tool_status") == "paused":
                pauses.append(decision)
    pauses = list(_unique_pauses(pauses))
    count = summary.get("security_pause_count")
    if not isinstance(count, int):
        count = len(pauses)
    lines = [f"Паузы безопасности: {count}"]
    if not pauses:
        lines.append("- нет")
        return tuple(lines)
    for pause in pauses[:5]:
        risk = _value_text(pause.get("risk"), "риск не указан")
        action = pause.get("action") or pause.get("message_ru") or pause.get("reason")
        lines.append(f"- {risk}: {_short_text(_value_text(action, 'детали не указаны'), 180)}")
    if len(pauses) > 5:
        lines.append(f"- еще {len(pauses) - 5} пауз не показано")
    return tuple(lines)


def _unique_pauses(pauses: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Mapping[str, Any]] = []
    for pause in pauses:
        key = (
            str(pause.get("phase") or ""),
            str(pause.get("risk") or ""),
            str(pause.get("action") or pause.get("message_ru") or pause.get("reason") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(pause)
    return tuple(unique)


def _context_lines(
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> tuple[str, ...]:
    metrics_items: list[Mapping[str, Any]] = []
    for event in events:
        if _event_name(event) not in _CONTEXT_EVENTS:
            continue
        details = _event_details(event)
        metrics = details.get("metrics")
        if isinstance(metrics, Mapping):
            metrics_items.append(metrics)
    explicit_count = summary.get("context_budget_events")
    count = explicit_count if isinstance(explicit_count, int) else len(metrics_items)
    if not metrics_items:
        return (f"Контекст: {count} событий; подробные метрики не записаны",)
    before_values = [item.get("before_tokens") for item in metrics_items]
    after_values = [item.get("after_tokens") for item in metrics_items]
    before_numbers = [value for value in before_values if isinstance(value, int | float)]
    after_numbers = [value for value in after_values if isinstance(value, int | float)]
    before = int(max(before_numbers)) if before_numbers else 0
    after = int(min(after_numbers)) if after_numbers else 0
    emergency = any(bool(item.get("emergency_compression_applied")) for item in metrics_items)
    kept = _sum_metric(metrics_items, "observation_sections_kept")
    dropped = _sum_metric(metrics_items, "observation_sections_dropped")
    parts = [f"Контекст: {count} событий; максимум {before} -> {after} токенов"]
    if kept or dropped:
        parts.append(f"секции сохранены/сброшены: {kept}/{dropped}")
    parts.append(f"экстренное сжатие: {'да' if emergency else 'нет'}")
    return ("; ".join(parts),)


def _notes_lines(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    final_text = payload.get("final_summary_ru")
    final = _mapping(payload.get("final"))
    if not final_text:
        final_text = final.get("summary_ru") or final.get("failure_ru")
    if not final_text:
        for event in reversed(events):
            details = _event_details(event)
            candidate = details.get("answer") or details.get("message") or details.get("summary")
            if isinstance(candidate, str) and candidate.strip():
                final_text = candidate
                break
    notes = list(_sequence_of_mappings(payload.get("final_notes") or payload.get("notes")))
    lines = ["Итог/заметки:"]
    if final_text:
        lines.append(f"- {_short_text(str(final_text), 360)}")
    for note in notes[:5]:
        lines.append(f"- {_short_text(_note_text(note), 260)}")
    if not final_text and not notes:
        lines.append("- не записаны")
    if len(notes) > 5:
        lines.append(f"- еще {len(notes) - 5} заметок не показано")
    return tuple(lines)


def _blocker_lines(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> tuple[str, ...]:
    blockers: list[Mapping[str, Any]] = list(_sequence_of_mappings(payload.get("blockers")))
    for event in events:
        if _event_name(event) in _BLOCKER_EVENTS:
            blockers.append(_event_details(event))
    count = summary.get("blocker_count")
    if not isinstance(count, int):
        count = len(blockers)
    lines = [f"Блокеры: {count}"]
    if not blockers:
        lines.append("- нет")
        return tuple(lines)
    for blocker in blockers[:5]:
        blocker_type = blocker.get("blocker_type") or blocker.get("phase") or blocker.get("message")
        lines.append(f"- {_short_text(_value_text(blocker_type, 'детали не указаны'), 180)}")
    return tuple(lines)


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_sequence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(str(item) for item in value if item)


def _sum_metric(metrics: Sequence[Mapping[str, Any]], key: str) -> int:
    return int(
        sum(value for item in metrics if isinstance((value := item.get(key)), int | float))
    )


def _note_text(note: Mapping[str, Any]) -> str:
    preferred_keys = ("title", "item_name", "subject", "summary", "reason", "classification")
    parts = [str(note[key]) for key in preferred_keys if note.get(key)]
    if parts:
        return "; ".join(parts)
    return "; ".join(f"{key}: {value}" for key, value in list(note.items())[:4])


def _value_text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _short_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."
