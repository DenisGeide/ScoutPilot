"""Planning Engine protocol definitions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from scout_pilot.models import ExecutionPlan, PageObservation, UserTask
from scout_pilot.tools.types import ToolSchema


class PlanningEngine(Protocol):
    """Create and revise plans from semantic observations."""

    async def create_plan(
        self,
        task: UserTask,
        observation: PageObservation | None,
        memory_summaries: Sequence[str] = (),
        available_tools: Sequence[ToolSchema] = (),
    ) -> ExecutionPlan:
        """Create an initial execution plan."""

    async def revise_plan(
        self,
        plan: ExecutionPlan,
        observation: PageObservation | None,
        reason: str,
        memory_summaries: Sequence[str] = (),
        available_tools: Sequence[ToolSchema] = (),
    ) -> ExecutionPlan:
        """Revise a plan after new evidence or failure."""
