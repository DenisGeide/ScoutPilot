"""Hierarchical Memory protocol definitions."""

from __future__ import annotations

from typing import Protocol, Sequence

from scout_pilot.memory.types import MemorySnapshot, MemoryUpdateResult
from scout_pilot.models import MemoryLayer, MemoryRecord


class MemoryStore(Protocol):
    """Store privacy-aware memory records across runtime scopes."""

    async def remember(self, record: MemoryRecord) -> None:
        """Persist a memory record."""

    async def recall(self, scope: str) -> Sequence[MemoryRecord]:
        """Recall memory records for a scope."""

    async def update(self, record: MemoryRecord) -> MemoryUpdateResult:
        """Persist a memory record and return filtering metadata."""

    def recall_layer(
        self,
        layer: MemoryLayer,
        scope: str | None = None,
        limit: int | None = None,
    ) -> Sequence[MemoryRecord]:
        """Recall records from one memory layer."""

    def snapshot(self, scope: str) -> MemorySnapshot:
        """Return a bounded memory snapshot."""

    def context_summaries(
        self,
        scope: str,
        max_items: int | None = None,
    ) -> Sequence[str]:
        """Return compact summaries suitable for LLM context."""
