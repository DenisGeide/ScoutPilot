"""Provider-neutral LLM and reasoning models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from scout_pilot.models import PageObservation, ToolRequest
from scout_pilot.tools.types import ToolSchema


class LlmProviderName(str, Enum):
    """Supported runtime LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    MOCK = "mock"


class LlmMessageRole(str, Enum):
    """Provider-neutral message role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LlmFinishReason(str, Enum):
    """Provider-neutral model stop reason."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    ERROR = "error"
    UNKNOWN = "unknown"


class LlmErrorCode(str, Enum):
    """Normalized provider error code."""

    RATE_LIMIT = "rate_limit"
    INVALID_CREDENTIALS = "invalid_credentials"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN = "unknown"


class ReasoningStatus(str, Enum):
    """High-level reasoning outcome for the planner/runtime."""

    TOOL_SELECTED = "tool_selected"
    ANSWER = "answer"
    NEEDS_OBSERVATION = "needs_observation"
    NEEDS_CONFIRMATION = "needs_confirmation"
    FAILURE = "failure"


@dataclass(frozen=True)
class LlmMessage:
    """Provider-neutral chat message."""

    role: LlmMessageRole
    content: str


@dataclass(frozen=True)
class LlmToolCall:
    """Provider-neutral tool call emitted by a model."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmUsage:
    """Provider-neutral token usage."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LlmProviderRequest:
    """Provider-neutral request sent to a concrete LLM adapter."""

    messages: tuple[LlmMessage, ...]
    tools: tuple[ToolSchema, ...] = ()
    model: str | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None

    def __init__(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolSchema] = (),
        model: str | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        object.__setattr__(self, "messages", tuple(messages))
        object.__setattr__(self, "tools", tuple(tools))
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "max_output_tokens", max_output_tokens)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)


@dataclass(frozen=True)
class LlmProviderResponse:
    """Provider-neutral successful model response."""

    content: str | None = None
    tool_calls: tuple[LlmToolCall, ...] = ()
    finish_reason: LlmFinishReason = LlmFinishReason.UNKNOWN
    usage: LlmUsage = field(default_factory=LlmUsage)
    raw_provider_name: str | None = None


@dataclass(frozen=True)
class LlmProviderError:
    """Provider-neutral model failure."""

    code: LlmErrorCode
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class LlmProviderResult:
    """Provider-neutral model result."""

    success: bool
    response: LlmProviderResponse | None = None
    error: LlmProviderError | None = None


@dataclass(frozen=True)
class ReasoningContext:
    """Bounded context supplied to the Reasoning Engine."""

    user_task: str
    observation: PageObservation | None
    memory_summaries: tuple[str, ...] = ()
    available_tools: tuple[ToolSchema, ...] = ()
    security_constraints: tuple[str, ...] = ()
    confirmation_constraints: tuple[str, ...] = ()
    budget: Mapping[str, int] = field(default_factory=dict)

    def __init__(
        self,
        user_task: str,
        observation: PageObservation | None,
        memory_summaries: Sequence[str] = (),
        available_tools: Sequence[ToolSchema] = (),
        security_constraints: Sequence[str] = (),
        confirmation_constraints: Sequence[str] = (),
        budget: Mapping[str, int] | None = None,
    ) -> None:
        if not user_task.strip():
            raise ValueError("user_task cannot be empty")
        object.__setattr__(self, "user_task", user_task)
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "memory_summaries", tuple(memory_summaries))
        object.__setattr__(self, "available_tools", tuple(available_tools))
        object.__setattr__(self, "security_constraints", tuple(security_constraints))
        object.__setattr__(self, "confirmation_constraints", tuple(confirmation_constraints))
        object.__setattr__(self, "budget", dict(budget or {}))


@dataclass(frozen=True)
class ReasoningResult:
    """Provider-neutral reasoning decision."""

    status: ReasoningStatus
    message: str
    selected_tool: ToolRequest | None = None
    answer: str | None = None
    provider_error: LlmProviderError | None = None

    @classmethod
    def tool_selected(cls, request: ToolRequest, message: str = "Tool selected.") -> "ReasoningResult":
        return cls(status=ReasoningStatus.TOOL_SELECTED, message=message, selected_tool=request)

    @classmethod
    def answer(cls, text: str) -> "ReasoningResult":
        return cls(status=ReasoningStatus.ANSWER, message="Answer produced.", answer=text)

    @classmethod
    def needs_observation(cls, message: str) -> "ReasoningResult":
        return cls(status=ReasoningStatus.NEEDS_OBSERVATION, message=message)

    @classmethod
    def needs_confirmation(cls, message: str) -> "ReasoningResult":
        return cls(status=ReasoningStatus.NEEDS_CONFIRMATION, message=message)

    @classmethod
    def failure(cls, message: str, error: LlmProviderError | None = None) -> "ReasoningResult":
        return cls(status=ReasoningStatus.FAILURE, message=message, provider_error=error)
