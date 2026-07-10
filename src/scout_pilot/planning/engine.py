"""Planning Engine protocol definitions."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.models import ExecutionPlan, PageObservation, UserTask


class PlanningEngine(Protocol):
    """Create and revise plans from semantic observations."""

    async def create_plan(self, task: UserTask, observation: PageObservation) -> ExecutionPlan:
        """Create an initial execution plan."""

    async def revise_plan(
        self,
        plan: ExecutionPlan,
        observation: PageObservation,
        reason: str,
    ) -> ExecutionPlan:
        """Revise a plan after new evidence or failure."""
