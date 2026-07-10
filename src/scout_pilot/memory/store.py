"""Hierarchical Memory protocol definitions."""

from __future__ import annotations

from typing import Protocol, Sequence

from scout_pilot.models import MemoryRecord


class MemoryStore(Protocol):
    """Store privacy-aware memory records across runtime scopes."""

    async def remember(self, record: MemoryRecord) -> None:
        """Persist a memory record."""

    async def recall(self, scope: str) -> Sequence[MemoryRecord]:
        """Recall memory records for a scope."""
