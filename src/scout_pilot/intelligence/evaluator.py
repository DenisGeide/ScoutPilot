"""Execution Intelligence protocol definitions."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.models import ExecutionPlan, PageObservation


class ExecutionEvaluator(Protocol):
    """Evaluate progress and recommend recovery decisions."""

    async def needs_recovery(self, plan: ExecutionPlan, observation: PageObservation) -> bool:
        """Return whether the runtime should recover or replan."""
