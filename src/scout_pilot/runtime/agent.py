"""Autonomous Agent Runtime protocol definitions."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from scout_pilot.models import RuntimeEvent, UserTask


class AgentRuntime(Protocol):
    """Coordinate the autonomous agent lifecycle."""

    async def run(self, task: UserTask) -> AsyncIterator[RuntimeEvent]:
        """Run a task and stream structured runtime events."""
