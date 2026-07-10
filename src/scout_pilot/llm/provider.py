"""Provider-neutral LLM provider protocol definitions."""

from __future__ import annotations

from typing import Protocol

from scout_pilot.llm.types import LlmProviderRequest, LlmProviderResult


class LlmProvider(Protocol):
    """Provider-neutral interface implemented by concrete LLM adapters."""

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        """Return a provider-neutral completion result."""
