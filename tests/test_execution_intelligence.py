import asyncio
from datetime import datetime, timezone

from scout_pilot.intelligence import (
    DeterministicExecutionEvaluator,
    PlanValidity,
    RecoveryAction,
    StepEvaluationContext,
    StepOutcome,
)
from scout_pilot.models import (
    ExecutionPlan,
    InteractiveElement,
    PageIssue,
    PageIssueCode,
    PageObservation,
    PlanStep,
    ToolRequest,
    UserTask,
)
from scout_pilot.tools.types import ToolExecutionResult, ToolExecutionStatus, ToolFailureKind


def test_evaluator_classifies_success_when_semantic_state_changes():
    evaluator = DeterministicExecutionEvaluator()
    plan = _plan()

    evaluation = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=plan,
                step=plan.steps[0],
                tool_request=_request(),
                tool_result=_tool_result(success=True),
                before_observation=_observation("Before", "Ready"),
                after_observation=_observation("After", "Clicked"),
            )
        )
    )

    assert evaluation.outcome is StepOutcome.SUCCESS
    assert evaluation.recommended_action is RecoveryAction.CONTINUE
    assert evaluation.page_changed is True
    assert evaluation.moved_forward is True
    assert evaluation.plan_validity is PlanValidity.VALID
    assert "raw" not in evaluation.reflection_summary.lower()


def test_evaluator_observes_again_for_first_noop_then_replans_for_repeated_observation():
    evaluator = DeterministicExecutionEvaluator()
    plan = _plan()
    same = _observation("Same", "No visible change")

    first = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=plan,
                step=plan.steps[0],
                tool_request=_request(),
                tool_result=_tool_result(success=True),
                before_observation=same,
                after_observation=same,
            )
        )
    )
    second = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=plan,
                step=plan.steps[0],
                tool_request=_request(),
                tool_result=_tool_result(success=True),
                before_observation=same,
                after_observation=same,
            )
        )
    )

    assert first.outcome is StepOutcome.UNCERTAIN
    assert first.recommended_action is RecoveryAction.OBSERVE_AGAIN
    assert second.recommended_action is RecoveryAction.REPLAN
    assert second.metrics.repeated_observation_count == 2


def test_evaluator_recommends_retry_for_navigation_timeout():
    evaluator = DeterministicExecutionEvaluator()

    evaluation = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=_plan(tool_name="browser.navigate"),
                step=_plan(tool_name="browser.navigate").steps[0],
                tool_request=ToolRequest("browser.navigate", {"url": "https://example.test"}),
                tool_result=_tool_result(
                    tool_name="browser.navigate",
                    success=False,
                    retryable=True,
                    failure_kind=ToolFailureKind.BROWSER,
                    error_code="navigation_timeout",
                ),
                before_observation=_observation("Before", "Home"),
                after_observation=_observation("Before", "Home"),
            )
        )
    )

    assert evaluation.outcome is StepOutcome.FAILURE
    assert evaluation.recommended_action is RecoveryAction.RETRY
    assert "Navigation" in " ".join(evaluation.reasons)


def test_evaluator_replans_when_semantic_element_disappears():
    evaluator = DeterministicExecutionEvaluator()

    evaluation = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=_plan(),
                step=_plan().steps[0],
                tool_request=_request(),
                tool_result=_tool_result(
                    success=False,
                    failure_kind=ToolFailureKind.BROWSER,
                    error_code="semantic_element_not_found",
                ),
                before_observation=_observation("Before", "Button visible"),
                after_observation=_observation("After", "Button gone"),
            )
        )
    )

    assert evaluation.outcome is StepOutcome.FAILURE
    assert evaluation.recommended_action is RecoveryAction.REPLAN
    assert evaluation.alternative_actions


def test_evaluator_detects_page_issues_and_invalid_plans():
    evaluator = DeterministicExecutionEvaluator()
    invalid_plan = ExecutionPlan(
        task=UserTask("Do something"),
        steps=[PlanStep("Broken step", tool_request=_request())],
        validation_errors=["Plan references an unavailable tool."],
    )
    blocked = _observation(
        "Blocked",
        "Access denied",
        issues=(PageIssue(PageIssueCode.BLOCKED_PAGE, "Blocked page."),),
    )

    needs_recovery = asyncio.run(evaluator.needs_recovery(invalid_plan, blocked))
    evaluation = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=invalid_plan,
                step=invalid_plan.steps[0],
                tool_request=_request(),
                tool_result=_tool_result(success=True),
                before_observation=_observation("Before", "Ready"),
                after_observation=blocked,
            )
        )
    )

    assert needs_recovery is True
    assert evaluation.plan_validity is PlanValidity.INVALID
    assert evaluation.recommended_action is RecoveryAction.STOP


def test_evaluator_requests_confirmation_for_confirmable_step():
    evaluator = DeterministicExecutionEvaluator()
    request = _request()
    step = PlanStep(
        "Submit selected action",
        tool_request=request,
        requires_confirmation=True,
    )
    plan = ExecutionPlan(task=UserTask("Submit action"), steps=[step])

    evaluation = asyncio.run(
        evaluator.evaluate_step(
            StepEvaluationContext(
                plan=plan,
                step=step,
                tool_request=request,
                tool_result=_tool_result(success=True),
                before_observation=_observation("Before", "Ready"),
                after_observation=_observation("After", "Submitted"),
            )
        )
    )

    assert evaluation.outcome is StepOutcome.UNCERTAIN
    assert evaluation.recommended_action is RecoveryAction.REQUEST_CONFIRMATION
    assert evaluation.confirmation_required is True


def _plan(tool_name: str = "browser.click") -> ExecutionPlan:
    request = ToolRequest(tool_name, {"element_id": "el_1"})
    return ExecutionPlan(
        task=UserTask("Click the action"),
        steps=[PlanStep("Click action", tool_request=request)],
    )


def _request() -> ToolRequest:
    return ToolRequest("browser.click", {"element_id": "el_1"})


def _observation(
    title: str,
    summary: str,
    *,
    issues=(),
) -> PageObservation:
    return PageObservation(
        url="https://example.test",
        title=title,
        summary=summary,
        interactive_elements=[
            InteractiveElement(
                element_id="el_1",
                role="button",
                accessible_name="Action",
                visible_text="Action",
            )
        ],
        issues=issues,
    )


def _tool_result(
    *,
    tool_name: str = "browser.click",
    success: bool,
    retryable: bool = False,
    status: ToolExecutionStatus = ToolExecutionStatus.SUCCESS,
    failure_kind: ToolFailureKind | None = None,
    error_code: str | None = None,
) -> ToolExecutionResult:
    now = datetime.now(tz=timezone.utc)
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status if not success else ToolExecutionStatus.SUCCESS,
        success=success,
        message="Tool completed." if success else "Tool failed.",
        failure_kind=failure_kind,
        retryable=retryable,
        error_code=error_code,
        started_at=now,
        finished_at=now,
    )
