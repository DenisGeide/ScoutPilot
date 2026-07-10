"""LLM provider protocol definitions."""

from __future__ import annotations

from typing import Protocol, Sequence

from scout_pilot.models import PageObservation, ToolRequest


class LlmProvider(Protocol):
    """Provider-neutral interface for future OpenAI and Anthropic adapters."""

    async def propose_next_action(
        self,
        task: str,
        observation: PageObservation,
        available_tools: Sequence[str],
    ) -> ToolRequest:
        """Return the next proposed tool request without executing it."""
