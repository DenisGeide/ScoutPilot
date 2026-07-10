"""Hierarchical memory support types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from scout_pilot.models import MemoryRecord


@dataclass(frozen=True)
class MemorySettings:
    """Limits for bounded hierarchical memory."""

    max_working_records: int = 8
    max_task_records: int = 64
    max_episodic_records: int = 24
    max_context_summaries: int = 10
    max_value_chars: int = 600
    max_summary_chars: int = 240


@dataclass(frozen=True)
class MemoryUpdateResult:
    """Result of a memory update after privacy filtering."""

    accepted: bool
    record: MemoryRecord | None = None
    reason: str = ""
    redacted: bool = False
    compressed: bool = False


@dataclass(frozen=True)
class MemorySnapshot:
    """Bounded view of all memory layers for a scope."""

    working: tuple[MemoryRecord, ...] = ()
    task: tuple[MemoryRecord, ...] = ()
    episodic: tuple[MemoryRecord, ...] = ()
    summaries: tuple[str, ...] = ()

    def __init__(
        self,
        working: Sequence[MemoryRecord] = (),
        task: Sequence[MemoryRecord] = (),
        episodic: Sequence[MemoryRecord] = (),
        summaries: Sequence[str] = (),
    ) -> None:
        object.__setattr__(self, "working", tuple(working))
        object.__setattr__(self, "task", tuple(task))
        object.__setattr__(self, "episodic", tuple(episodic))
        object.__setattr__(self, "summaries", tuple(summaries))
