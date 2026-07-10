"""Typed domain models shared across layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4


class ActionRisk(str, Enum):
    """Security classification for actions before tool execution."""

    SAFE = "safe"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class RuntimeStatus(str, Enum):
    """High-level runtime state."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanStepStatus(str, Enum):
    """Execution state for a single plan step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class UserTask:
    """Natural-language task supplied by the user."""

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("User task text cannot be empty")


@dataclass(frozen=True)
class SemanticElement:
    """Compact semantic representation of an observable page element."""

    role: str
    label: str | None = None
    description: str | None = None
    index: int | None = None
    is_interactive: bool = False


@dataclass(frozen=True)
class PageObservation:
    """LLM-facing page summary that intentionally excludes raw HTML."""

    url: str | None
    title: str | None
    summary: str
    elements: tuple[SemanticElement, ...] = field(default_factory=tuple)

    def __init__(
        self,
        url: str | None,
        title: str | None,
        summary: str,
        elements: Sequence[SemanticElement] = (),
    ) -> None:
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "elements", tuple(elements))

    def to_llm_context(self) -> Mapping[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
            "elements": [
                {
                    "role": element.role,
                    "label": element.label,
                    "description": element.description,
                    "index": element.index,
                    "is_interactive": element.is_interactive,
                }
                for element in self.elements
            ],
        }


@dataclass(frozen=True)
class ToolRequest:
    """Provider-neutral request to execute a named tool."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    risk: ActionRisk = ActionRisk.SAFE


@dataclass(frozen=True)
class ToolResult:
    """Provider-neutral result returned by a tool."""

    name: str
    success: bool
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanStep:
    """One planned action in an agent workflow."""

    goal: str
    status: PlanStepStatus = PlanStepStatus.PENDING
    tool_request: ToolRequest | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    """Current plan for completing a user task."""

    task: UserTask
    steps: tuple[PlanStep, ...] = field(default_factory=tuple)

    def __init__(self, task: UserTask, steps: Sequence[PlanStep] = ()) -> None:
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "steps", tuple(steps))


@dataclass(frozen=True)
class ContextBudget:
    """Current context budget used before sending content to an LLM."""

    max_tokens: int
    reserved_tokens: int = 0
    used_tokens: int = 0

    @property
    def remaining_tokens(self) -> int:
        return max(self.max_tokens - self.reserved_tokens - self.used_tokens, 0)


@dataclass(frozen=True)
class MemoryRecord:
    """A privacy-aware memory item."""

    key: str
    value: Mapping[str, Any]
    scope: str
    contains_private_data: bool = False


@dataclass(frozen=True)
class ConfirmationRequest:
    """User confirmation required before sensitive tool execution."""

    reason: str
    tool_request: ToolRequest
    confirmation_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class RuntimeEvent:
    """Structured internal runtime event."""

    name: str
    status: RuntimeStatus
    details: Mapping[str, Any] = field(default_factory=dict)
