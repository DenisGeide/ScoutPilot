"""LLM Provider layer."""

from scout_pilot.llm.config import LlmProviderConfig
from scout_pilot.llm.factory import create_llm_provider
from scout_pilot.llm.mock_provider import (
    DeterministicBrowserMockProvider,
    DeterministicLocalDemoMockProvider,
    MockLlmProvider,
)
from scout_pilot.llm.provider import LlmProvider
from scout_pilot.llm.reasoning import ReasoningEngine, ReasoningSettings
from scout_pilot.llm.tool_adapters import AnthropicToolSchemaAdapter, OpenAIToolSchemaAdapter
from scout_pilot.llm.types import (
    LlmErrorCode,
    LlmFinishReason,
    LlmMessage,
    LlmMessageRole,
    LlmProviderError,
    LlmProviderName,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
    LlmUsage,
    ReasoningContext,
    ReasoningResult,
    ReasoningStatus,
)

__all__ = [
    "AnthropicToolSchemaAdapter",
    "DeterministicBrowserMockProvider",
    "DeterministicLocalDemoMockProvider",
    "LlmErrorCode",
    "LlmFinishReason",
    "LlmMessage",
    "LlmMessageRole",
    "LlmProvider",
    "LlmProviderConfig",
    "LlmProviderError",
    "LlmProviderName",
    "LlmProviderRequest",
    "LlmProviderResponse",
    "LlmProviderResult",
    "LlmToolCall",
    "LlmUsage",
    "MockLlmProvider",
    "OpenAIToolSchemaAdapter",
    "ReasoningContext",
    "ReasoningEngine",
    "ReasoningResult",
    "ReasoningSettings",
    "ReasoningStatus",
    "create_llm_provider",
]
