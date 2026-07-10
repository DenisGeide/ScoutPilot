"""Deterministic Execution Intelligence evaluator."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from scout_pilot.intelligence.types import (
    ExecutionMetrics,
    PlanValidity,
    ProgressEvaluation,
    RecoveryAction,
    StepEvaluation,
    StepEvaluationContext,
    StepOutcome,
)
from scout_pilot.models import (
    ExecutionPlan,
    PageIssueCode,
    PageObservation,
    PlanStepStatus,
)
from scout_pilot.tools.types import ToolExecutionResult, ToolExecutionStatus, ToolFailureKind


class ExecutionEvaluator(Protocol):
    """Evaluate progress and recommend recovery decisions."""

    async def evaluate_step(self, context: StepEvaluationContext) -> StepEvaluation:
        """Evaluate a just-finished tool execution."""

    async def needs_recovery(self, plan: ExecutionPlan, observation: PageObservation) -> bool:
        """Return whether the runtime should recover or replan."""


@dataclass
class DeterministicExecutionEvaluator:
    """Rule-based evaluator for website-neutral recovery decisions."""

    max_repeated_failures: int = 2
    max_repeated_observations: int = 2
    max_no_progress_actions: int = 2

    def __post_init__(self) -> None:
        self._last_failure_signature: tuple[str, str, str] | None = None
        self._repeated_failure_count = 0
        self._last_observation_signature: tuple[object, ...] | None = None
        self._repeated_observation_count = 0
        self._consecutive_no_progress_count = 0

    async def evaluate_step(self, context: StepEvaluationContext) -> StepEvaluation:
        """Evaluate one tool result without provider or browser implementation access."""

        page_changed = _observation_signature(context.before_observation) != _observation_signature(
            context.after_observation
        )
        moved_forward = context.tool_result.success and page_changed
        if context.step is not None and context.step.status is PlanStepStatus.COMPLETED:
            moved_forward = True

        metrics = self._update_metrics(context.tool_result, context.after_observation, moved_forward)
        progress = _progress_for_plan(context.plan)
        plan_validity = _plan_validity(context.plan)
        reasons: list[str] = []
        alternatives: list[str] = []
        page_issue_action = _action_for_page_issues(context.after_observation)
        if page_issue_action is not None:
            reasons.append("Current observation contains a blocking page issue.")
            alternatives.append("Refresh the semantic observation and build a new website-neutral plan.")
            evaluation = _evaluation(
                outcome=StepOutcome.FAILURE,
                action=page_issue_action,
                validity=_failed_plan_validity(plan_validity),
                progress=progress,
                page_changed=page_changed,
                moved_forward=False,
                confirmation_required=False,
                reasons=reasons,
                alternatives=alternatives,
                metrics=metrics,
                tool_result=context.tool_result,
            )
            self._update_no_progress(evaluation.moved_forward)
            return evaluation

        if context.tool_result.status is ToolExecutionStatus.PAUSED:
            reasons.append("Tool runtime paused execution before continuing.")
            return _evaluation(
                outcome=StepOutcome.UNCERTAIN,
                action=RecoveryAction.REQUEST_CONFIRMATION,
                validity=plan_validity,
                progress=progress,
                page_changed=page_changed,
                moved_forward=False,
                confirmation_required=True,
                reasons=reasons,
                alternatives=("Wait for explicit user confirmation.",),
                metrics=metrics,
                tool_result=context.tool_result,
            )

        if not context.tool_result.success:
            outcome, action, extra_reasons, extra_alternatives = _failure_decision(
                context.tool_result,
                metrics,
                self.max_repeated_failures,
            )
            reasons.extend(extra_reasons)
            alternatives.extend(extra_alternatives)
            evaluation = _evaluation(
                outcome=outcome,
                action=action,
                validity=_failed_plan_validity(plan_validity),
                progress=progress,
                page_changed=page_changed,
                moved_forward=False,
                confirmation_required=False,
                reasons=reasons,
                alternatives=alternatives,
                metrics=metrics,
                tool_result=context.tool_result,
            )
            self._update_no_progress(evaluation.moved_forward)
            return evaluation

        if not page_changed and _expects_page_change(context.tool_result.tool_name):
            reasons.append("Tool succeeded but the semantic observation did not change.")
            action = RecoveryAction.REPLAN
            alternatives.append("Choose a different semantic element or route through the planner.")
            if (
                metrics.repeated_observation_count < self.max_repeated_observations
                and metrics.consecutive_no_progress_count < self.max_no_progress_actions
            ):
                action = RecoveryAction.OBSERVE_AGAIN
                alternatives.append("Observe again before changing the plan.")
            evaluation = _evaluation(
                outcome=StepOutcome.UNCERTAIN,
                action=action,
                validity=PlanValidity.DEGRADED,
                progress=progress,
                page_changed=False,
                moved_forward=False,
                confirmation_required=False,
                reasons=reasons,
                alternatives=alternatives,
                metrics=metrics,
                tool_result=context.tool_result,
            )
            self._update_no_progress(evaluation.moved_forward)
            return evaluation

        if plan_validity is PlanValidity.INVALID:
            reasons.append("The current plan is invalid after the action.")
            alternatives.append("Rebuild the plan from the latest semantic observation.")
            evaluation = _evaluation(
                outcome=StepOutcome.UNCERTAIN,
                action=RecoveryAction.REPLAN,
                validity=plan_validity,
                progress=progress,
                page_changed=page_changed,
                moved_forward=moved_forward,
                confirmation_required=False,
                reasons=reasons,
                alternatives=alternatives,
                metrics=metrics,
                tool_result=context.tool_result,
            )
            self._update_no_progress(evaluation.moved_forward)
            return evaluation

        reasons.append("Tool succeeded and the semantic state changed." if page_changed else "Tool succeeded.")
        evaluation = _evaluation(
            outcome=StepOutcome.SUCCESS,
            action=RecoveryAction.CONTINUE,
            validity=plan_validity,
            progress=progress,
            page_changed=page_changed,
            moved_forward=moved_forward or not _expects_page_change(context.tool_result.tool_name),
            confirmation_required=False,
            reasons=reasons,
            alternatives=(),
            metrics=metrics,
            tool_result=context.tool_result,
        )
        self._update_no_progress(evaluation.moved_forward)
        return evaluation

    async def needs_recovery(self, plan: ExecutionPlan, observation: PageObservation) -> bool:
        """Return whether current evidence suggests the plan should be revised."""

        if _plan_validity(plan) is PlanValidity.INVALID:
            return True
        if _has_issue(observation, PageIssueCode.BLOCKED_PAGE, PageIssueCode.NAVIGATION_ERROR):
            return True
        if _has_issue(observation, PageIssueCode.EMPTY_PAGE) and plan.steps:
            return True
        return False

    def _update_metrics(
        self,
        result: ToolExecutionResult,
        observation: PageObservation | None,
        moved_forward: bool,
    ) -> ExecutionMetrics:
        failure_signature = _failure_signature(result)
        if failure_signature is None:
            self._last_failure_signature = None
            self._repeated_failure_count = 0
        elif failure_signature == self._last_failure_signature:
            self._repeated_failure_count += 1
        else:
            self._last_failure_signature = failure_signature
            self._repeated_failure_count = 1

        observation_signature = _observation_signature(observation)
        if observation_signature is None:
            self._last_observation_signature = None
            self._repeated_observation_count = 0
        elif observation_signature == self._last_observation_signature:
            self._repeated_observation_count += 1
        else:
            self._last_observation_signature = observation_signature
            self._repeated_observation_count = 1

        if moved_forward:
            self._consecutive_no_progress_count = 0
        else:
            self._consecutive_no_progress_count += 1

        return ExecutionMetrics(
            repeated_failure_count=self._repeated_failure_count,
            repeated_observation_count=self._repeated_observation_count,
            consecutive_no_progress_count=self._consecutive_no_progress_count,
        )

    def _update_no_progress(self, moved_forward: bool) -> None:
        if moved_forward:
            self._consecutive_no_progress_count = 0


def _evaluation(
    *,
    outcome: StepOutcome,
    action: RecoveryAction,
    validity: PlanValidity,
    progress: ProgressEvaluation,
    page_changed: bool,
    moved_forward: bool,
    confirmation_required: bool,
    reasons: Sequence[str],
    alternatives: Sequence[str],
    metrics: ExecutionMetrics,
    tool_result: ToolExecutionResult,
) -> StepEvaluation:
    summary = _reflection_summary(
        tool_result=tool_result,
        outcome=outcome,
        action=action,
        page_changed=page_changed,
        moved_forward=moved_forward,
        reasons=reasons,
    )
    return StepEvaluation(
        outcome=outcome,
        recommended_action=action,
        plan_validity=validity,
        progress=progress,
        page_changed=page_changed,
        moved_forward=moved_forward,
        confirmation_required=confirmation_required,
        reasons=tuple(reasons),
        alternative_actions=tuple(alternatives),
        metrics=metrics,
        reflection_summary=summary,
    )


def _failure_decision(
    result: ToolExecutionResult,
    metrics: ExecutionMetrics,
    max_repeated_failures: int,
) -> tuple[StepOutcome, RecoveryAction, tuple[str, ...], tuple[str, ...]]:
    error_code = result.error_code or ""
    reasons = [f"Tool failed with status {result.status.value}."]
    alternatives: list[str] = []

    if result.failure_kind is ToolFailureKind.SECURITY:
        reasons.append("Tool Runtime reported a security boundary decision.")
        return (
            StepOutcome.FAILURE,
            RecoveryAction.REQUEST_CONFIRMATION
            if result.status is ToolExecutionStatus.PAUSED
            else RecoveryAction.STOP,
            tuple(reasons),
            ("Wait for Security Policy or user confirmation before continuing.",),
        )

    if error_code == "semantic_element_not_found":
        reasons.append("The semantic element disappeared or is no longer valid.")
        alternatives.append("Re-observe the page and select an element from the latest semantic IDs.")
        return StepOutcome.FAILURE, RecoveryAction.REPLAN, tuple(reasons), tuple(alternatives)

    if error_code in {"invalid_url", "invalid_key", "invalid_wait_duration", "invalid_field_value"}:
        reasons.append("The tool request is invalid and should not be retried unchanged.")
        alternatives.append("Ask the planner for a different semantic action.")
        return StepOutcome.FAILURE, RecoveryAction.REPLAN, tuple(reasons), tuple(alternatives)

    if "navigation" in error_code:
        reasons.append("Navigation did not complete successfully.")
        alternatives.append("Try a discovered or user-provided URL instead of repeating the same request.")
        action = RecoveryAction.RETRY if result.retryable else RecoveryAction.REPLAN
        return StepOutcome.FAILURE, action, tuple(reasons), tuple(alternatives)

    if metrics.repeated_failure_count >= max_repeated_failures:
        reasons.append("The same failure repeated.")
        alternatives.append("Choose a different tool or rebuild the plan.")
        return StepOutcome.FAILURE, RecoveryAction.REPLAN, tuple(reasons), tuple(alternatives)

    if result.retryable:
        reasons.append("Tool failure is marked retryable.")
        alternatives.append("Retry after a fresh observation or revised plan.")
        return StepOutcome.FAILURE, RecoveryAction.RETRY, tuple(reasons), tuple(alternatives)

    reasons.append("Failure is not retryable.")
    alternatives.append("Stop or replan before touching the browser again.")
    return StepOutcome.FAILURE, RecoveryAction.STOP, tuple(reasons), tuple(alternatives)


def _action_for_page_issues(observation: PageObservation | None) -> RecoveryAction | None:
    if observation is None:
        return None
    if _has_issue(observation, PageIssueCode.BLOCKED_PAGE):
        return RecoveryAction.STOP
    if _has_issue(observation, PageIssueCode.NAVIGATION_ERROR):
        return RecoveryAction.REPLAN
    return None


def _failed_plan_validity(validity: PlanValidity) -> PlanValidity:
    if validity is PlanValidity.INVALID:
        return validity
    return PlanValidity.DEGRADED


def _plan_validity(plan: ExecutionPlan | None) -> PlanValidity:
    if plan is None:
        return PlanValidity.UNCERTAIN
    if plan.validation_errors:
        return PlanValidity.INVALID
    if not plan.steps:
        return PlanValidity.INVALID

    progress = _progress_for_plan(plan)
    if progress.failed_steps:
        return PlanValidity.DEGRADED
    if progress.pending_steps == 0 and progress.completed_steps < progress.total_steps:
        return PlanValidity.INVALID
    return PlanValidity.VALID


def _progress_for_plan(plan: ExecutionPlan | None) -> ProgressEvaluation:
    steps = plan.steps if plan is not None else ()
    completed = sum(1 for step in steps if step.status is PlanStepStatus.COMPLETED)
    failed = sum(1 for step in steps if step.status is PlanStepStatus.FAILED)
    pending = sum(1 for step in steps if step.status is PlanStepStatus.PENDING)
    return ProgressEvaluation(
        completed_steps=completed,
        failed_steps=failed,
        pending_steps=pending,
        total_steps=len(steps),
    )


def _observation_signature(observation: PageObservation | None) -> tuple[object, ...] | None:
    if observation is None:
        return None
    interactive = tuple(
        (item.element_id, item.role, item.accessible_name, item.visible_text, item.target_url)
        for item in observation.interactive_elements
    )
    fields = tuple(
        (field.field_id, field.role, field.label, field.placeholder, field.value_state)
        for field in observation.form_fields
    )
    dialogs = tuple((dialog.role, dialog.title, dialog.text) for dialog in observation.dialogs)
    issues = tuple(issue.code.value for issue in observation.issues)
    sections = tuple((section.role, section.heading, section.text) for section in observation.sections)
    return (
        observation.url,
        observation.title,
        observation.summary,
        interactive,
        fields,
        dialogs,
        issues,
        sections,
    )


def _failure_signature(result: ToolExecutionResult) -> tuple[str, str, str] | None:
    if result.success:
        return None
    reason = result.error_code or (
        result.failure_kind.value if result.failure_kind is not None else ""
    )
    return (
        result.tool_name,
        result.status.value,
        reason,
    )


def _expects_page_change(tool_name: str) -> bool:
    return tool_name not in {
        "browser.observe",
        "browser.screenshot",
        "browser.wait",
    }


def _has_issue(observation: PageObservation, *codes: PageIssueCode) -> bool:
    code_set = set(codes)
    return any(issue.code in code_set for issue in observation.issues)


def _reflection_summary(
    *,
    tool_result: ToolExecutionResult,
    outcome: StepOutcome,
    action: RecoveryAction,
    page_changed: bool,
    moved_forward: bool,
    reasons: Sequence[str],
) -> str:
    reason_text = reasons[0] if reasons else "No additional reason."
    return (
        f"{tool_result.tool_name} evaluated as {outcome.value}; "
        f"page_changed={page_changed}; moved_forward={moved_forward}; "
        f"recommended_action={action.value}; {reason_text}"
    )
