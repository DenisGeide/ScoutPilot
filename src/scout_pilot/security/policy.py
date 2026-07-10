"""Security Policy protocol definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scout_pilot.models import ActionRisk, ToolRequest


@dataclass(frozen=True)
class SecurityDecision:
    """Deterministic decision made before a tool can execute."""

    risk: ActionRisk
    allowed: bool
    requires_confirmation: bool
    reason: str


class SecurityPolicy(Protocol):
    """Classify and gate tool requests independently from the LLM."""

    def evaluate(self, request: ToolRequest) -> SecurityDecision:
        """Return a deterministic security decision for a tool request."""
