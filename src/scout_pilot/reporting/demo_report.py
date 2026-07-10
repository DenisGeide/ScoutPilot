"""Safe JSON report support for local and live demonstrations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DemoReportRecorder:
    """Collect a bounded, HTML-free report for a demonstration run."""

    def __init__(
        self,
        *,
        demo_name: str,
        task: str,
        start_url: str,
    ) -> None:
        self._payload: dict[str, Any] = {
            "demo_name": demo_name,
            "task": task,
            "start_url": start_url,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "events": [],
            "notes": [],
            "security_pauses": [],
            "stopped_before_side_effects": True,
            "final_summary_ru": None,
            "stop_reason": None,
            "success": False,
        }

    @property
    def security_pauses(self) -> tuple[Mapping[str, Any], ...]:
        pauses = self._payload["security_pauses"]
        return tuple(dict(item) for item in pauses)

    def record_event(self, kind: str, **details: Any) -> None:
        self._payload["events"].append(
            {
                "kind": kind,
                "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
                **_json_safe(details),
            }
        )

    def record_security_pause(self, **details: Any) -> None:
        pause = {
            "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
            **_json_safe(details),
        }
        self._payload["security_pauses"].append(pause)
        self.record_event("security_pause", **pause)

    def record_note(self, note: Mapping[str, Any]) -> None:
        self._payload["notes"].append(_json_safe(dict(note)))

    def set_final(self, *, success: bool, stop_reason: str, summary_ru: str) -> None:
        self._payload["success"] = success
        self._payload["stop_reason"] = stop_reason
        self._payload["final_summary_ru"] = summary_ru

    def to_dict(self) -> Mapping[str, Any]:
        return _json_safe(self._payload)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and not isinstance(value, str):
        return value.value
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
