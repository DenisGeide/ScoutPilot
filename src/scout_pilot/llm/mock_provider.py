"""Mock LLM provider for deterministic tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from scout_pilot.llm.provider import LlmProvider
from scout_pilot.llm.types import LlmProviderRequest, LlmProviderResult


@dataclass
class MockLlmProvider:
    """Deterministic provider that returns queued results and records requests."""

    results: deque[LlmProviderResult] = field(default_factory=deque)
    requests: list[LlmProviderRequest] = field(default_factory=list)

    def __init__(self, results: list[LlmProviderResult] | None = None) -> None:
        self.results = deque(results or [])
        self.requests = []

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        self.requests.append(request)
        if not self.results:
            raise AssertionError("MockLlmProvider has no queued result.")
        return self.results.popleft()


def assert_provider_protocol(provider: LlmProvider) -> LlmProvider:
    """Return provider unchanged while helping tests type-check protocol usage."""

    return provider
