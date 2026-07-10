"""Planning Engine support types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlanValidationSeverity(str, Enum):
    """Severity for plan validation issues."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class PlanValidationIssue:
    """One validation issue found in a generated plan."""

    code: str
    message: str
    severity: PlanValidationSeverity = PlanValidationSeverity.ERROR
    step_id: str | None = None


@dataclass(frozen=True)
class PlanValidationResult:
    """Validation result for a generated execution plan."""

    issues: tuple[PlanValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> tuple[PlanValidationIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity is PlanValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[PlanValidationIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity is PlanValidationSeverity.WARNING
        )

    def error_messages(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.errors)

    def warning_messages(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.warnings)


@dataclass(frozen=True)
class PlanningSettings:
    """Planner limits and provider request settings."""

    max_steps: int = 6
    max_input_tokens: int = 8000
    max_prompt_observation_chars: int = 6000
    max_prompt_observation_tokens: int = 3000
    max_memory_tokens: int = 1200
    max_memory_summaries: int = 5
    max_tool_schemas: int = 20
    max_output_tokens: int = 1600
    timeout_seconds: float = 20.0
