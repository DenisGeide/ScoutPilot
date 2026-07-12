"""Autonomous Agent Runtime models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from scout_pilot.models import ExecutionPlan, RuntimeStatus, UserTask


DEFAULT_MAX_AGENT_STEPS = 24


class AgentState(str, Enum):
    """State machine states for the autonomous runtime."""

    IDLE = "idle"
    PLANNING = "planning"
    OBSERVING = "observing"
    REASONING = "reasoning"
    EXECUTING = "executing"
    EVALUATING = "evaluating"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    RETRYING = "retrying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskTerminationReason(str, Enum):
    """Explainable termination reasons for a task."""

    ANSWERED = "answered"
    CANCELLED = "cancelled"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    MAX_ITERATIONS_EXCEEDED = "max_iterations_exceeded"
    MAX_FAILURES_EXCEEDED = "max_failures_exceeded"
    TOOL_FAILURE = "tool_failure"
    REASONING_FAILURE = "reasoning_failure"
    PAGE_BLOCKER = "page_blocker"
    FATAL_ERROR = "fatal_error"


@dataclass(frozen=True)
class RuntimeSettings:
    """Runtime loop limits."""

    max_iterations: int = DEFAULT_MAX_AGENT_STEPS
    max_failures: int = 3
    max_memory_summaries: int = 10


@dataclass(frozen=True)
class AgentProgress:
    """Structured progress counters for runtime events."""

    iteration: int
    max_iterations: int
    failure_count: int
    max_failures: int
    completed_steps: int = 0
    total_steps: int = 0

    def to_dict(self) -> Mapping[str, int]:
        return {
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "failure_count": self.failure_count,
            "max_failures": self.max_failures,
            "completed_steps": self.completed_steps,
            "total_steps": self.total_steps,
        }


@dataclass(frozen=True)
class AgentTaskResult:
    """Final task result produced by the autonomous runtime."""

    task_id: str
    task: UserTask
    status: RuntimeStatus
    final_state: AgentState
    success: bool
    termination_reason: TaskTerminationReason
    message: str
    answer: str | None = None
    iterations: int = 0
    failures: int = 0
    plan: ExecutionPlan | None = None
    confirmation_request: Mapping[str, object] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.final_state in {
            AgentState.COMPLETED,
            AgentState.CANCELLED,
            AgentState.FAILED,
            AgentState.WAITING_FOR_CONFIRMATION,
        }
