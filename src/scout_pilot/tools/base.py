"""Base provider-neutral tool interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scout_pilot.browser.engine import BrowserEngine
from scout_pilot.observation.engine import ObservationEngine
from scout_pilot.tools.types import (
    ToolExecutionOutcome,
    ToolInputSchema,
    ToolOutputSchema,
)


@dataclass(frozen=True)
class ToolContext:
    """Runtime dependencies available to tool implementations."""

    browser: BrowserEngine | None = None
    observation_engine: ObservationEngine | None = None


class BaseTool(Protocol):
    """Provider-neutral tool implementation contract."""

    name: str
    description: str
    input_schema: ToolInputSchema
    output_schema: ToolOutputSchema
    timeout_seconds: float

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        """Execute a validated tool input."""
