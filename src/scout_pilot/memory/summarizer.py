"""Provider-neutral memory summarization hooks."""

from __future__ import annotations

import json
from typing import Protocol, Sequence

from scout_pilot.memory.types import MemorySettings
from scout_pilot.models import MemoryRecord


class MemorySummarizer(Protocol):
    """Create compact memory summaries without provider-specific logic."""

    def summarize(self, records: Sequence[MemoryRecord]) -> str:
        """Return a compact summary for records."""


class DeterministicMemorySummarizer:
    """Stable summarizer used by default and in tests."""

    def __init__(self, settings: MemorySettings | None = None) -> None:
        self._settings = settings or MemorySettings()

    def summarize(self, records: Sequence[MemoryRecord]) -> str:
        fragments = [_format_record(record) for record in records if record.value]
        text = "; ".join(fragment for fragment in fragments if fragment)
        if len(text) > self._settings.max_summary_chars:
            return text[: self._settings.max_summary_chars].rstrip() + "..."
        return text


def _format_record(record: MemoryRecord) -> str:
    preferred_keys = ("summary", "goal", "constraint", "choice", "warning", "event", "text")
    for key in preferred_keys:
        value = record.value.get(key)
        if isinstance(value, str) and value.strip():
            return f"{record.kind.value}: {value.strip()}"
    serialized = json.dumps(record.value, ensure_ascii=False, sort_keys=True)
    return f"{record.kind.value}: {serialized}"
