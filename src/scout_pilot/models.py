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
    CANCELLED = "cancelled"
    FAILED = "failed"


class PlanStepStatus(str, Enum):
    """Execution state for a single plan step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoryLayer(str, Enum):
    """Independent memory layers used by the agent."""

    WORKING = "working"
    TASK = "task"
    EPISODIC = "episodic"


class MemoryRecordKind(str, Enum):
    """Typed memory record categories."""

    USER_GOAL = "user_goal"
    CONSTRAINT = "constraint"
    CONFIRMED_CHOICE = "confirmed_choice"
    WARNING = "warning"
    OBSERVATION = "observation"
    EVENT = "event"
    SUMMARY = "summary"
    FACT = "fact"


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


class PageIssueCode(str, Enum):
    """LLM-facing page issue signals."""

    LOADING = "loading"
    EMPTY_PAGE = "empty_page"
    MODAL_DIALOG = "modal_dialog"
    COOKIE_BANNER = "cookie_banner"
    LOGIN_WALL = "login_wall"
    CAPTCHA_BLOCKING_PAGE = "captcha_blocking_page"
    REGION_PROMPT = "region_prompt"
    BLOCKED_PAGE = "blocked_page"
    NAVIGATION_ERROR = "navigation_error"
    OBSERVATION_TRUNCATED = "observation_truncated"
    OBSERVATION_ERROR = "observation_error"


@dataclass(frozen=True)
class PageMetadata:
    """Page metadata that is safe to send to an LLM."""

    url: str | None
    title: str | None
    origin: str | None
    load_state: str
    is_visible: bool
    viewport_width: int | None = None
    viewport_height: int | None = None


@dataclass(frozen=True)
class ElementLocation:
    """Approximate element location relative to the visible viewport."""

    region: str
    x_ratio: float | None = None
    y_ratio: float | None = None
    width_ratio: float | None = None
    height_ratio: float | None = None


@dataclass(frozen=True)
class ElementState:
    """Accessible state summary for a page element."""

    disabled: bool = False
    checked: bool | None = None
    expanded: bool | None = None
    pressed: bool | None = None
    selected: bool | None = None
    required: bool = False
    readonly: bool = False


@dataclass(frozen=True)
class SemanticSection:
    """Visible content section after deduplication and compression."""

    section_id: str
    role: str
    heading: str | None
    text: str
    location: ElementLocation | None = None


@dataclass(frozen=True)
class InteractiveElement:
    """Visible interactive element with a generated stable ID."""

    element_id: str
    role: str
    accessible_name: str | None
    visible_text: str | None
    state: ElementState = field(default_factory=ElementState)
    location: ElementLocation | None = None
    target_url: str | None = None
    input_type: str | None = None


@dataclass(frozen=True)
class FormFieldSummary:
    """Form field summary that never includes the field value."""

    field_id: str
    role: str
    input_type: str | None
    label: str | None
    placeholder: str | None
    value_state: str
    state: ElementState = field(default_factory=ElementState)
    location: ElementLocation | None = None
    field_name: str | None = None


@dataclass(frozen=True)
class FocusedElementSummary:
    """Focused element summary with sensitive values redacted."""

    role: str
    accessible_name: str | None
    visible_text: str | None
    input_type: str | None = None
    value_state: str | None = None


@dataclass(frozen=True)
class DialogSummary:
    """Visible dialog or modal summary."""

    dialog_id: str
    role: str
    title: str | None
    text: str
    location: ElementLocation | None = None


@dataclass(frozen=True)
class PageIssue:
    """Issue signal detected while observing the current page."""

    code: PageIssueCode
    message: str
    severity: str = "info"


@dataclass(frozen=True)
class PageObservation:
    """LLM-facing page summary that intentionally excludes raw HTML."""

    url: str | None
    title: str | None
    summary: str
    metadata: PageMetadata | None = None
    sections: tuple[SemanticSection, ...] = field(default_factory=tuple)
    interactive_elements: tuple[InteractiveElement, ...] = field(default_factory=tuple)
    form_fields: tuple[FormFieldSummary, ...] = field(default_factory=tuple)
    focused_element: FocusedElementSummary | None = None
    dialogs: tuple[DialogSummary, ...] = field(default_factory=tuple)
    issues: tuple[PageIssue, ...] = field(default_factory=tuple)
    limits: Mapping[str, int] = field(default_factory=dict)
    elements: tuple[SemanticElement, ...] = field(default_factory=tuple)

    def __init__(
        self,
        url: str | None,
        title: str | None,
        summary: str,
        elements: Sequence[SemanticElement] = (),
        metadata: PageMetadata | None = None,
        sections: Sequence[SemanticSection] = (),
        interactive_elements: Sequence[InteractiveElement] = (),
        form_fields: Sequence[FormFieldSummary] = (),
        focused_element: FocusedElementSummary | None = None,
        dialogs: Sequence[DialogSummary] = (),
        issues: Sequence[PageIssue] = (),
        limits: Mapping[str, int] | None = None,
    ) -> None:
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "elements", tuple(elements))
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "sections", tuple(sections))
        object.__setattr__(self, "interactive_elements", tuple(interactive_elements))
        object.__setattr__(self, "form_fields", tuple(form_fields))
        object.__setattr__(self, "focused_element", focused_element)
        object.__setattr__(self, "dialogs", tuple(dialogs))
        object.__setattr__(self, "issues", tuple(issues))
        object.__setattr__(self, "limits", dict(limits or {}))

    def to_llm_context(self) -> Mapping[str, Any]:
        context: dict[str, Any] = {
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
        }
        if self.metadata is not None:
            context["metadata"] = _metadata_to_context(self.metadata)
        if self.sections:
            context["sections"] = [
                _section_to_context(section) for section in self.sections
            ]
        if self.interactive_elements:
            context["interactive_elements"] = [
                _interactive_element_to_context(element)
                for element in self.interactive_elements
            ]
        if self.form_fields:
            context["form_fields"] = [
                _form_field_to_context(field) for field in self.form_fields
            ]
        if self.focused_element is not None:
            context["focused_element"] = _focused_element_to_context(self.focused_element)
        if self.dialogs:
            context["dialogs"] = [_dialog_to_context(dialog) for dialog in self.dialogs]
        if self.issues:
            context["issues"] = [
                {"code": issue.code.value, "message": issue.message, "severity": issue.severity}
                for issue in self.issues
            ]
        if self.limits:
            context["limits"] = dict(self.limits)
        if self.elements:
            context["elements"] = [
                {
                    "role": element.role,
                    "label": element.label,
                    "description": element.description,
                    "index": element.index,
                    "is_interactive": element.is_interactive,
                }
                for element in self.elements
            ]
        return context


def _metadata_to_context(metadata: PageMetadata) -> Mapping[str, Any]:
    return {
        "url": metadata.url,
        "title": metadata.title,
        "origin": metadata.origin,
        "load_state": metadata.load_state,
        "is_visible": metadata.is_visible,
        "viewport_width": metadata.viewport_width,
        "viewport_height": metadata.viewport_height,
    }


def _location_to_context(location: ElementLocation | None) -> Mapping[str, Any] | None:
    if location is None:
        return None
    return {
        "region": location.region,
        "x_ratio": location.x_ratio,
        "y_ratio": location.y_ratio,
        "width_ratio": location.width_ratio,
        "height_ratio": location.height_ratio,
    }


def _state_to_context(state: ElementState) -> Mapping[str, Any]:
    return {
        "disabled": state.disabled,
        "checked": state.checked,
        "expanded": state.expanded,
        "pressed": state.pressed,
        "selected": state.selected,
        "required": state.required,
        "readonly": state.readonly,
    }


def _section_to_context(section: SemanticSection) -> Mapping[str, Any]:
    return {
        "id": section.section_id,
        "role": section.role,
        "heading": section.heading,
        "text": section.text,
        "location": _location_to_context(section.location),
    }


def _interactive_element_to_context(element: InteractiveElement) -> Mapping[str, Any]:
    return {
        "id": element.element_id,
        "role": element.role,
        "accessible_name": element.accessible_name,
        "visible_text": element.visible_text,
        "state": _state_to_context(element.state),
        "location": _location_to_context(element.location),
        "target_url": element.target_url,
        "input_type": element.input_type,
    }


def _form_field_to_context(field: FormFieldSummary) -> Mapping[str, Any]:
    return {
        "id": field.field_id,
        "role": field.role,
        "input_type": field.input_type,
        "label": field.label,
        "placeholder": field.placeholder,
        "value_state": field.value_state,
        "state": _state_to_context(field.state),
        "location": _location_to_context(field.location),
        "field_name": field.field_name,
    }


def _focused_element_to_context(element: FocusedElementSummary) -> Mapping[str, Any]:
    return {
        "role": element.role,
        "accessible_name": element.accessible_name,
        "visible_text": element.visible_text,
        "input_type": element.input_type,
        "value_state": element.value_state,
    }


def _dialog_to_context(dialog: DialogSummary) -> Mapping[str, Any]:
    return {
        "id": dialog.dialog_id,
        "role": dialog.role,
        "title": dialog.title,
        "text": dialog.text,
        "location": _location_to_context(dialog.location),
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
    step_id: str = field(default_factory=lambda: uuid4().hex)
    tool_name: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    rationale: str | None = None
    requires_confirmation: bool = False
    is_uncertain: bool = False
    uncertainty_reason: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.step_id:
            object.__setattr__(self, "step_id", uuid4().hex)
        object.__setattr__(self, "arguments", dict(self.arguments))
        if self.tool_request is not None:
            if self.tool_name is None:
                object.__setattr__(self, "tool_name", self.tool_request.name)
            if not self.arguments:
                object.__setattr__(self, "arguments", dict(self.tool_request.arguments))


@dataclass(frozen=True)
class ExecutionPlan:
    """Current plan for completing a user task."""

    task: UserTask
    steps: tuple[PlanStep, ...] = field(default_factory=tuple)
    summary: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    validation_errors: tuple[str, ...] = field(default_factory=tuple)
    source: str = "planner"
    observation_url: str | None = None
    observation_summary: str | None = None
    memory_summaries: tuple[str, ...] = field(default_factory=tuple)
    is_fallback: bool = False
    revision_reason: str | None = None

    def __init__(
        self,
        task: UserTask,
        steps: Sequence[PlanStep] = (),
        summary: str = "",
        warnings: Sequence[str] = (),
        validation_errors: Sequence[str] = (),
        source: str = "planner",
        observation_url: str | None = None,
        observation_summary: str | None = None,
        memory_summaries: Sequence[str] = (),
        is_fallback: bool = False,
        revision_reason: str | None = None,
    ) -> None:
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "steps", tuple(steps))
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "warnings", tuple(warnings))
        object.__setattr__(self, "validation_errors", tuple(validation_errors))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "observation_url", observation_url)
        object.__setattr__(self, "observation_summary", observation_summary)
        object.__setattr__(self, "memory_summaries", tuple(memory_summaries))
        object.__setattr__(self, "is_fallback", is_fallback)
        object.__setattr__(self, "revision_reason", revision_reason)


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
    layer: MemoryLayer = MemoryLayer.TASK
    kind: MemoryRecordKind = MemoryRecordKind.FACT
    importance: int = 1
    source: str | None = None
    record_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Memory record key cannot be empty")
        if not self.scope.strip():
            raise ValueError("Memory record scope cannot be empty")
        object.__setattr__(self, "value", dict(self.value))
        object.__setattr__(self, "layer", MemoryLayer(self.layer))
        object.__setattr__(self, "kind", MemoryRecordKind(self.kind))
        object.__setattr__(self, "importance", max(int(self.importance), 0))
        if not self.record_id:
            object.__setattr__(self, "record_id", uuid4().hex)


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
