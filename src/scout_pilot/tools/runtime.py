"""Provider-neutral tool runtime protocol."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.models import ToolRequest, ToolResult


class ToolRuntime(Protocol):
    """Execute registered tools through a stable request/result contract."""

    async def execute(self, request: ToolRequest) -> ToolResult:
        """Execute a tool request after policy checks have passed."""
