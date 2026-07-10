"""Safe report and replay artifacts for CLI runtime sessions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scout_pilot.models import RuntimeEvent


_RAW_HTML_PATTERN = re.compile(
    r"(?is)<!doctype\s+html|<html[\s>]|<body[\s>]|<script[\s>]|</[a-z][a-z0-9:-]*>"
)
_ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|token|secret|cookie|api[_-]?key|authorization)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
_SENSITIVE_KEY_HINTS = (
    "api_key",
    "authorization",
    "browser_profile",
    "cookie",
    "dom",
    "html",
    "password",
    "private_file",
    "raw",
    "resume",
    "secret",
    "screenshot",
    "session",
    "storage_state",
    "token",
)


@dataclass(frozen=True)
class RuntimeReportArtifacts:
    """Paths written for one CLI task run."""

    report_path: Path
    replay_path: Path


class RuntimeReportRecorder:
    """Record sanitized runtime events and write report/replay JSON files."""

    def __init__(
        self,
        *,
        task: str,
        mode: str,
        dry_run: bool,
    ) -> None:
        self._task = _sanitize_text(task)
        self._mode = mode
        self._dry_run = dry_run
        self._started_at = datetime.now(tz=timezone.utc)
        self._events: list[dict[str, Any]] = []
        self._final: dict[str, Any] | None = None

    async def record(self, event: RuntimeEvent) -> None:
        """Record a runtime event through the ReplayRecorder protocol."""

        self.record_event(event)

    def record_event(self, event: RuntimeEvent) -> None:
        """Record one sanitized event synchronously."""

        self._events.append(
            {
                "name": event.name,
                "status": event.status.value,
                "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
                "details": _sanitize_value(event.details),
            }
        )

    def finalize(
        self,
        *,
        success: bool,
        summary_ru: str,
        failure_ru: str | None = None,
    ) -> None:
        self._final = {
            "success": success,
            "summary_ru": _sanitize_text(summary_ru),
            "failure_ru": _sanitize_text(failure_ru) if failure_ru else None,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    def to_report_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "artifact_kind": "runtime_report",
            "task": self._task,
            "mode": self._mode,
            "dry_run": self._dry_run,
            "started_at": self._started_at.isoformat(),
            "event_count": len(self._events),
            "events": list(self._events),
            "final": dict(self._final or {}),
            "safety": {
                "raw_html_included": False,
                "sensitive_values_redacted": True,
                "private_browser_artifacts_included": False,
            },
        }

    def to_replay_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": 1,
            "artifact_kind": "runtime_replay",
            "task": self._task,
            "mode": self._mode,
            "dry_run": self._dry_run,
            "started_at": self._started_at.isoformat(),
            "events": list(self._events),
        }

    def write(self, *, report_path: Path, replay_path: Path) -> RuntimeReportArtifacts:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(self.to_report_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        replay_path.write_text(
            json.dumps(self.to_replay_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return RuntimeReportArtifacts(report_path=report_path, replay_path=replay_path)


def sanitize_for_report(value: Any) -> Any:
    """Return a JSON-safe value with private fields redacted."""

    return _sanitize_value(value)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _sanitize_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return "[REDACTED_PATH]"
    if hasattr(value, "value") and not isinstance(value, str):
        return _sanitize_value(value.value)
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _sanitize_text(str(value))


def _sanitize_text(text: str) -> str:
    if _RAW_HTML_PATTERN.search(text):
        return "[REDACTED_RAW_HTML]"
    redacted = _ASSIGNMENT_SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    return _truncate(redacted, 1200)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(hint in normalized for hint in _SENSITIVE_KEY_HINTS)


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."
