"""LLM provider configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from scout_pilot.llm.types import LlmProviderName

if TYPE_CHECKING:
    from scout_pilot.config import AppConfig


@dataclass(frozen=True)
class LlmProviderConfig:
    """Configuration shared by LLM provider adapters."""

    provider: LlmProviderName
    model: str
    timeout_seconds: float
    max_output_tokens: int
    api_key: str | None = None

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "LlmProviderConfig":
        provider = LlmProviderName(config.llm_provider.lower())
        api_key = None
        if provider is LlmProviderName.OPENAI:
            api_key = config.provider_secrets.openai_api_key
        elif provider is LlmProviderName.ANTHROPIC:
            api_key = config.provider_secrets.anthropic_api_key
        return cls(
            provider=provider,
            model=config.llm_model,
            timeout_seconds=config.llm_timeout_seconds,
            max_output_tokens=config.llm_max_output_tokens,
            api_key=api_key,
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ValueError(f"API key is not configured for provider: {self.provider.value}")
        return self.api_key
