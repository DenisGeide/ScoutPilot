"""Typed Execution Intelligence models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from scout_pilot.models import ExecutionPlan, PageObservation, PlanStep, ToolRequest
from scout_pilot.tools.types import ToolExecutionResult


class StepOutcome(str, Enum):
    """Deterministic classification for one action outcome."""

    SUCCESS = "success"
    FAILURE = "failure"
    UNCERTAIN = "uncertain"


class PlanValidity(str, Enum):
    """Whether the current plan is still useful."""

    VALID = "valid"
    DEGRADED = "degraded"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"


class RecoveryAction(str, Enum):
    """Recommended runtime action after reflection."""

    CONTINUE = "continue"
    OBSERVE_AGAIN = "observe_again"
    RETRY = "retry"
    REPLAN = "replan"
    REQUEST_CONFIRMATION = "request_confirmation"
    STOP = "stop"


@dataclass(frozen=True)
class ProgressEvaluation:
    """Plan progress derived from step statuses."""

    completed_steps: int
    failed_steps: int
    pending_steps: int
    total_steps: int

    @property
    def completed_ratio(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return self.completed_steps / self.total_steps

    def to_dict(self) -> Mapping[str, int | float]:
        return {
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "pending_steps": self.pending_steps,
            "total_steps": self.total_steps,
            "completed_ratio": self.completed_ratio,
        }


@dataclass(frozen=True)
class ExecutionMetrics:
    """Repeated pattern counters used by runtime recovery."""

    repeated_failure_count: int = 0
    repeated_observation_count: int = 0
    consecutive_no_progress_count: int = 0

    def to_dict(self) -> Mapping[str, int]:
        return {
            "repeated_failure_count": self.repeated_failure_count,
            "repeated_observation_count": self.repeated_observation_count,
            "consecutive_no_progress_count": self.consecutive_no_progress_count,
        }


@dataclass(frozen=True)
class StepEvaluationContext:
    """Inputs needed to evaluate a just-finished tool execution."""

    plan: ExecutionPlan | None
    tool_request: ToolRequest
    tool_result: ToolExecutionResult
    before_observation: PageObservation | None
    after_observation: PageObservation | None
    step: PlanStep | None = None


@dataclass(frozen=True)
class StepEvaluation:
    """Reflection result for one tool execution."""

    outcome: StepOutcome
    recommended_action: RecoveryAction
    plan_validity: PlanValidity
    progress: ProgressEvaluation
    page_changed: bool
    moved_forward: bool
    confirmation_required: bool = False
    reasons: tuple[str, ...] = ()
    alternative_actions: tuple[str, ...] = ()
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    reflection_summary: str = ""

    @property
    def needs_replan(self) -> bool:
        return self.recommended_action is RecoveryAction.REPLAN

    @property
    def should_retry(self) -> bool:
        return self.recommended_action is RecoveryAction.RETRY
