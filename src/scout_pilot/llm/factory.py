"""LLM provider factory."""

from __future__ import annotations

from scout_pilot.llm.anthropic_provider import AnthropicLlmProvider
from scout_pilot.llm.codex_cli_provider import CodexCliLlmProvider
from scout_pilot.llm.config import LlmProviderConfig
from scout_pilot.llm.openai_provider import OpenAILlmProvider
from scout_pilot.llm.provider import LlmProvider
from scout_pilot.llm.types import LlmProviderName


def create_llm_provider(config: LlmProviderConfig) -> LlmProvider:
    """Create a configured concrete provider adapter."""

    if config.provider is LlmProviderName.OPENAI:
        return OpenAILlmProvider(config)
    if config.provider is LlmProviderName.ANTHROPIC:
        return AnthropicLlmProvider(config)
    if config.provider is LlmProviderName.CODEX:
        return CodexCliLlmProvider()
    raise ValueError(f"Unsupported runtime provider: {config.provider.value}")
