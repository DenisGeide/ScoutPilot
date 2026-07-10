"""Reporting and replay protocol definitions."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.models import RuntimeEvent


class ReplayRecorder(Protocol):
    """Record safe runtime events for later replay or reports."""

    async def record(self, event: RuntimeEvent) -> None:
        """Record a non-private runtime event."""
