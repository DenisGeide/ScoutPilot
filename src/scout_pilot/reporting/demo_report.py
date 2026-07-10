"""Safe JSON report support for local and live demonstrations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scout_pilot.reporting.runtime_report import sanitize_for_report


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
            "schema_version": 1,
            "artifact_kind": "demo_report",
            "demo_name": demo_name,
            "task": task,
            "start_url": start_url,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "events": [],
            "notes": [],
            "final_notes": [],
            "discovered_urls": [],
            "pages_read": [],
            "blockers": [],
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

    def record_discovered_urls(self, urls: list[str | None] | tuple[str | None, ...]) -> None:
        existing = set(self._payload["discovered_urls"])
        for url in urls:
            if not url or url in existing:
                continue
            safe_url = _json_safe(url)
            self._payload["discovered_urls"].append(safe_url)
            existing.add(safe_url)

    def record_page_read(self, **details: Any) -> None:
        page = {
            "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
            **_json_safe(details),
        }
        self._payload["pages_read"].append(page)
        self.record_event("page_read", **page)

    def record_blocker(self, **details: Any) -> None:
        blocker = {
            "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
            **_json_safe(details),
        }
        self._payload["blockers"].append(blocker)
        self.record_event("page_blocker", **blocker)

    def record_note(self, note: Mapping[str, Any]) -> None:
        safe_note = _json_safe(dict(note))
        self._payload["notes"].append(safe_note)
        self._payload["final_notes"].append(safe_note)

    def set_final(self, *, success: bool, stop_reason: str, summary_ru: str) -> None:
        self._payload["success"] = success
        self._payload["stop_reason"] = stop_reason
        self._payload["final_summary_ru"] = summary_ru

    def to_dict(self) -> Mapping[str, Any]:
        payload = dict(self._payload)
        payload["summary"] = _summarize_payload(payload)
        return _json_safe(payload)

    def to_replay_dict(self) -> Mapping[str, Any]:
        payload = {
            "schema_version": 1,
            "artifact_kind": "demo_replay",
            "demo_name": self._payload["demo_name"],
            "task": self._payload["task"],
            "start_url": self._payload["start_url"],
            "generated_at": self._payload["generated_at"],
            "events": self._payload["events"],
            "notes": self._payload["notes"],
            "final_notes": self._payload["final_notes"],
            "discovered_urls": self._payload["discovered_urls"],
            "pages_read": self._payload["pages_read"],
            "blockers": self._payload["blockers"],
            "security_pauses": self._payload["security_pauses"],
            "final_summary_ru": self._payload["final_summary_ru"],
            "stop_reason": self._payload["stop_reason"],
            "success": self._payload["success"],
            "summary": _summarize_payload(self._payload),
        }
        return _json_safe(payload)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def write_replay(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_replay_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def _json_safe(value: Any) -> Any:
    return sanitize_for_report(value)


def _summarize_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    events = payload.get("events", [])
    if not isinstance(events, list):
        events = []
    event_kinds = [
        str(event.get("kind"))
        for event in events
        if isinstance(event, Mapping) and event.get("kind")
    ]
    selected_tools = [
        str(event.get("tool_name"))
        for event in events
        if isinstance(event, Mapping)
        and event.get("kind") == "selected_tool"
        and event.get("tool_name")
    ]
    return {
        "observation_count": event_kinds.count("observation"),
        "decision_count": event_kinds.count("decision"),
        "selected_tools": list(dict.fromkeys(selected_tools)),
        "tool_decision_count": event_kinds.count("selected_tool"),
        "tool_result_count": event_kinds.count("tool_result")
        + event_kinds.count("tool_result_after_confirmation"),
        "security_pause_count": len(payload.get("security_pauses", [])),
        "note_count": len(payload.get("notes", [])),
        "discovered_url_count": len(payload.get("discovered_urls", [])),
        "pages_read_count": len(payload.get("pages_read", [])),
        "blocker_count": len(payload.get("blockers", [])),
        "context_budget_events": event_kinds.count("context_budget"),
        "stopped_before_side_effects": bool(payload.get("stopped_before_side_effects")),
    }
