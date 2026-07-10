"""Semantic Observation Engine protocol definitions."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.models import PageObservation


class ObservationEngine(Protocol):
    """Build compact semantic observations from the current browser state."""

    async def observe(self) -> PageObservation:
        """Return an LLM-safe page observation."""
