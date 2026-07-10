"""Typed contracts for generic semantic navigation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class NavigationIntentKind(str, Enum):
    """Website-neutral navigation intent kinds."""

    CLICK = "click"
    FIELD = "field"
    SEARCH_FIELD = "search_field"
    NAVIGATE = "navigate"


class SemanticResolutionStatus(str, Enum):
    """Outcome of resolving an intent against a semantic observation."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INVALID = "invalid"


@dataclass(frozen=True)
class NavigationIntent:
    """User or planner intent expressed without selectors or routes."""

    kind: NavigationIntentKind
    target: str | None = None
    role: str | None = None
    context: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", NavigationIntentKind(self.kind))
        if self.target is not None:
            object.__setattr__(self, "target", self.target.strip() or None)
        if self.role is not None:
            object.__setattr__(self, "role", self.role.strip().casefold() or None)
        if self.context is not None:
            object.__setattr__(self, "context", self.context.strip() or None)


@dataclass(frozen=True)
class SemanticCandidate:
    """One semantic page candidate that may satisfy an intent."""

    candidate_id: str
    kind: str
    role: str
    name: str | None
    visible_text: str | None = None
    context: str | None = None
    target_url: str | None = None
    input_type: str | None = None
    location_region: str | None = None
    location_bucket: str | None = None
    fingerprint: str | None = None
    score: int = 0
    reasons: tuple[str, ...] = ()
    disabled: bool = False

    @property
    def element_id(self) -> str:
        return self.candidate_id

    def to_dict(self) -> Mapping[str, object]:
        return {
            "id": self.candidate_id,
            "kind": self.kind,
            "role": self.role,
            "name": self.name,
            "visible_text": self.visible_text,
            "context": self.context,
            "target_url": self.target_url,
            "input_type": self.input_type,
            "location_region": self.location_region,
            "location_bucket": self.location_bucket,
            "fingerprint": self.fingerprint,
            "score": self.score,
            "reasons": list(self.reasons),
            "disabled": self.disabled,
        }


@dataclass(frozen=True)
class SemanticResolution:
    """Resolution result for one navigation intent."""

    status: SemanticResolutionStatus
    intent: NavigationIntent
    selected: SemanticCandidate | None = None
    candidates: tuple[SemanticCandidate, ...] = ()
    message: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.status is SemanticResolutionStatus.RESOLVED and self.selected is not None

    def to_dict(self) -> Mapping[str, object]:
        return {
            "status": self.status.value,
            "intent": {
                "kind": self.intent.kind.value,
                "target": self.intent.target,
                "role": self.intent.role,
                "context": self.intent.context,
            },
            "selected": self.selected.to_dict() if self.selected else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "message": self.message,
        }


@dataclass(frozen=True)
class FormFillPlanStep:
    """One semantic form-fill planning step without storing the field value."""

    requested_label: str
    status: SemanticResolutionStatus
    field_id: str | None = None
    field_label: str | None = None
    message: str = ""
    candidates: tuple[SemanticCandidate, ...] = ()

    def to_dict(self) -> Mapping[str, object]:
        return {
            "requested_label": self.requested_label,
            "status": self.status.value,
            "field_id": self.field_id,
            "field_label": self.field_label,
            "message": self.message,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class FormFillPlan:
    """Website-neutral mapping from labels to semantic field IDs."""

    steps: tuple[FormFillPlanStep, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        return all(step.status is SemanticResolutionStatus.RESOLVED for step in self.steps)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "is_complete": self.is_complete,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class PageTransition:
    """Semantic page transition signal after an action."""

    changed: bool
    reason: str
    before_url: str | None = None
    after_url: str | None = None
    before_title: str | None = None
    after_title: str | None = None
    before_load_state: str | None = None
    after_load_state: str | None = None
    repeated: bool = False

    def to_dict(self) -> Mapping[str, object]:
        return {
            "changed": self.changed,
            "reason": self.reason,
            "before_url": self.before_url,
            "after_url": self.after_url,
            "before_title": self.before_title,
            "after_title": self.after_title,
            "before_load_state": self.before_load_state,
            "after_load_state": self.after_load_state,
            "repeated": self.repeated,
        }
