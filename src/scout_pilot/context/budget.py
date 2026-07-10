"""Context Budgeting and Compression protocol definitions."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.models import ContextBudget, PageObservation


class ContextBudgeter(Protocol):
    """Prepare page observations for bounded LLM context windows."""

    def estimate_observation_tokens(self, observation: PageObservation) -> int:
        """Estimate tokens required by an observation."""

    def fit_observation(
        self,
        observation: PageObservation,
        budget: ContextBudget,
    ) -> PageObservation:
        """Return an observation that fits the available context budget."""
