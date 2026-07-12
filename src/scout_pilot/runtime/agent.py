"""Autonomous Agent Runtime implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

from scout_pilot.intelligence.evaluator import (
    DeterministicExecutionEvaluator,
    ExecutionEvaluator,
)
from scout_pilot.intelligence.types import (
    RecoveryAction,
    StepEvaluation,
    StepEvaluationContext,
    StepOutcome,
)
from scout_pilot.llm.reasoning import ReasoningEngine
from scout_pilot.llm.types import LlmProviderError, ReasoningContext, ReasoningStatus
from scout_pilot.memory.store import MemoryStore
from scout_pilot.models import (
    ExecutionPlan,
    MemoryLayer,
    MemoryRecord,
    MemoryRecordKind,
    PageIssue,
    PageIssueCode,
    PageObservation,
    PlanStep,
    PlanStepStatus,
    RuntimeEvent,
    RuntimeStatus,
    ToolRequest,
    UserTask,
)
from scout_pilot.observation.engine import ObservationEngine
from scout_pilot.planning.engine import PlanningEngine
from scout_pilot.runtime.types import (
    AgentProgress,
    AgentState,
    AgentTaskResult,
    RuntimeSettings,
    TaskTerminationReason,
)
from scout_pilot.tools.runtime import ToolRuntime
from scout_pilot.tools.types import ToolExecutionResult, ToolExecutionStatus, ToolSchema


logger = logging.getLogger(__name__)


class AgentRuntime(Protocol):
    """Coordinate the autonomous agent lifecycle."""

    async def run(self, task: UserTask) -> AsyncIterator[RuntimeEvent]:
        """Run a task and stream structured runtime events."""


class AutonomousAgentRuntime:
    """Observe, reason, plan, act and terminate through bounded layer contracts."""

    def __init__(
        self,
        *,
        observation_engine: ObservationEngine,
        reasoning_engine: ReasoningEngine,
        planning_engine: PlanningEngine,
        tool_runtime: ToolRuntime,
        memory: MemoryStore,
        tool_schemas: Sequence[ToolSchema] = (),
        evaluator: ExecutionEvaluator | None = None,
        settings: RuntimeSettings | None = None,
        security_constraints: Sequence[str] = (),
        confirmation_constraints: Sequence[str] = (),
        budget: Mapping[str, int] | None = None,
    ) -> None:
        self._observation_engine = observation_engine
        self._reasoning_engine = reasoning_engine
        self._planning_engine = planning_engine
        self._tool_runtime = tool_runtime
        self._memory = memory
        self._tool_schemas = tuple(tool_schemas)
        self._evaluator = evaluator or DeterministicExecutionEvaluator()
        self._settings = settings or RuntimeSettings()
        self._security_constraints = tuple(security_constraints)
        self._confirmation_constraints = tuple(confirmation_constraints)
        self._budget = dict(budget or {})
        self._state = AgentState.IDLE
        self._cancel_requested = False
        self._cancel_reason = "Cancelled by user."
        self._pending_confirmation: Mapping[str, object] | None = None
        self.last_result: AgentTaskResult | None = None

    @property
    def state(self) -> AgentState:
        return self._state

    def cancel(self, reason: str = "Cancelled by user.") -> None:
        """Request clean cancellation at the next runtime checkpoint."""

        self._cancel_requested = True
        self._cancel_reason = reason

    @property
    def pending_confirmation(self) -> Mapping[str, object] | None:
        return self._pending_confirmation

    def confirm_pending_action(self, confirmation_id: str) -> bool:
        """Confirm one paused tool action without executing it automatically."""

        confirmer = getattr(self._tool_runtime, "confirm_pending_action", None)
        if not callable(confirmer):
            return False
        confirmed = bool(confirmer(confirmation_id))
        if confirmed:
            self._pending_confirmation = None
        return confirmed

    def reject_pending_action(self, confirmation_id: str) -> bool:
        """Reject one paused tool action."""

        rejecter = getattr(self._tool_runtime, "reject_pending_action", None)
        if not callable(rejecter):
            return False
        rejected = bool(rejecter(confirmation_id))
        if rejected and self._pending_confirmation:
            if self._pending_confirmation.get("confirmation_id") == confirmation_id:
                self._pending_confirmation = None
        return rejected

    async def run(self, task: UserTask) -> AsyncIterator[RuntimeEvent]:
        """Run one task and stream deterministic runtime events."""

        task_id = uuid4().hex
        progress = _progress(0, self._settings, 0, None)
        observation: PageObservation | None = None
        previous_observation_signature: tuple[object, ...] | None = None
        plan: ExecutionPlan | None = None
        failure_count = 0
        self._state = AgentState.IDLE
        self.last_result = None

        yield self._event(
            "task_started",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            progress=progress,
            message_key="runtime.task.started",
            details={"task": task.text},
        )
        await self._remember_task_goal(task_id, task)

        if self._cancel_requested:
            event, result = await self._cancel(task_id, task, progress, plan)
            self.last_result = result
            yield event
            return

        try:
            for iteration in range(1, self._settings.max_iterations + 1):
                progress = _progress(iteration, self._settings, failure_count, plan)

                transition = self._transition(
                    AgentState.OBSERVING,
                    "Start iteration by capturing semantic page state.",
                    task_id,
                    progress,
                )
                yield transition
                observation = await self._observation_engine.observe()
                observation_signature = _observation_signature(observation)
                if observation_signature == previous_observation_signature:
                    await self._remember_event(
                        task_id,
                        f"observation_unchanged_{iteration}",
                        (
                            "The semantic observation is unchanged. Do not request another "
                            "observation or browser.wait; answer from available evidence or "
                            "choose a different semantic tool."
                        ),
                    )
                previous_observation_signature = observation_signature
                await self._remember_observation(
                    task_id,
                    iteration,
                    observation,
                    phase="before_action",
                )
                yield self._event(
                    "observation_captured",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.observation.captured",
                    details={
                        "url": observation.url,
                        "title": observation.title,
                        "summary": observation.summary,
                    },
                )
                blocker_decision = _page_blocker_decision(observation)
                if blocker_decision is not None:
                    await self._remember_event(
                        task_id,
                        f"page_blocker_{iteration}_before_action",
                        str(blocker_decision["memory_summary"]),
                    )
                    yield self._event(
                        "page_blocker_detected",
                        RuntimeStatus.FAILED
                        if blocker_decision["stop"]
                        else RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.page_blocker.detected",
                        details=blocker_decision,
                    )
                    if blocker_decision["stop"]:
                        result = await self._fail(
                            task_id,
                            task,
                            progress,
                            plan,
                            TaskTerminationReason.PAGE_BLOCKER,
                            str(blocker_decision["message"]),
                            failure_count,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_failed",
                            RuntimeStatus.FAILED,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.task.failed",
                            details={
                                **_result_details(result),
                                "page_blocker": blocker_decision,
                            },
                        )
                        return

                if self._cancel_requested:
                    event, result = await self._cancel(task_id, task, progress, plan)
                    self.last_result = result
                    yield event
                    return

                if plan is None:
                    yield self._transition(
                        AgentState.PLANNING,
                        "Create initial execution plan from current observation.",
                        task_id,
                        progress,
                    )
                    memory_summaries = self._memory_summaries(task_id)
                    plan = await self._planning_engine.create_plan(
                        task,
                        observation,
                        memory_summaries=memory_summaries,
                        available_tools=self._tool_schemas,
                    )
                    budget_event = self._context_budget_event(
                        "planning",
                        self._planning_engine,
                        task_id,
                        progress,
                    )
                    if budget_event is not None:
                        yield budget_event
                    await self._remember_plan(task_id, plan)
                    progress = _progress(iteration, self._settings, failure_count, plan)
                    yield self._event(
                        "plan_created",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.plan.created",
                        details={
                            "summary": plan.summary,
                            "steps": len(plan.steps),
                            "current_plan_step": _first_pending_plan_step_summary(plan),
                            "warnings": list(plan.warnings),
                            "validation_errors": list(plan.validation_errors),
                        },
                    )

                yield self._transition(
                    AgentState.REASONING,
                    "Ask provider-neutral Reasoning Engine for next decision.",
                    task_id,
                    progress,
                )
                reasoning = await self._reason(task, task_id, observation)
                budget_event = self._context_budget_event(
                    "reasoning",
                    self._reasoning_engine,
                    task_id,
                    progress,
                )
                if budget_event is not None:
                    yield budget_event
                yield self._event(
                    "reasoning_completed",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.reasoning.completed",
                    details={
                        "status": reasoning.status.value,
                        "message": reasoning.message,
                        "provider_error": _provider_error_details(
                            reasoning.provider_error
                        ),
                    },
                )

                if reasoning.status is ReasoningStatus.ANSWER:
                    result = await self._complete(
                        task_id,
                        task,
                        progress,
                        plan,
                        reasoning.answer or reasoning.message,
                    )
                    self.last_result = result
                    yield self._event(
                        "task_completed",
                        RuntimeStatus.COMPLETED,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.task.completed",
                        details=_result_details(result),
                    )
                    return

                if reasoning.status is ReasoningStatus.NEEDS_CONFIRMATION:
                    result = await self._wait_for_confirmation(
                        task_id,
                        task,
                        progress,
                        plan,
                        reasoning.message,
                    )
                    self.last_result = result
                    yield self._event(
                        "confirmation_required",
                        RuntimeStatus.WAITING_FOR_CONFIRMATION,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.confirmation.required",
                        details=_result_details(result),
                    )
                    return

                if reasoning.status is ReasoningStatus.NEEDS_OBSERVATION:
                    await self._remember_event(
                        task_id,
                        f"reasoning_needs_observation_{iteration}",
                        f"Reasoning requested another observation: {reasoning.message}",
                    )
                    continue

                if reasoning.status is ReasoningStatus.FAILURE:
                    failure_count += 1
                    plan, events = await self._handle_failure(
                        task=task,
                        task_id=task_id,
                        observation=observation,
                        plan=plan,
                        failure_count=failure_count,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        reason=reasoning.message,
                    )
                    for event in events:
                        yield event
                    if failure_count >= self._settings.max_failures:
                        result = await self._fail(
                            task_id,
                            task,
                            _progress(iteration, self._settings, failure_count, plan),
                            plan,
                            TaskTerminationReason.REASONING_FAILURE,
                            reasoning.message,
                            failure_count,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_failed",
                            RuntimeStatus.FAILED,
                            task_id=task_id,
                            progress=_progress(iteration, self._settings, failure_count, plan),
                            message_key="runtime.task.failed",
                            details=_result_details(result),
                        )
                        return
                    continue

                if reasoning.selected_tool is None:
                    failure_count += 1
                    continue

                selected_tool = reasoning.selected_tool
                selected_plan_step = _find_plan_step(plan, selected_tool)
                yield self._event(
                    "tool_selected",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.tool.selected",
                    details={
                        "selected_tool": selected_tool.name,
                        "selected_tool_arguments": _redact_tool_arguments(
                            selected_tool,
                            self._tool_schemas,
                        ),
                        "current_plan_step": _plan_step_summary(selected_plan_step),
                        "next_action": "execute_tool",
                    },
                )
                yield self._transition(
                    AgentState.EXECUTING,
                    f"Execute selected tool {selected_tool.name}.",
                    task_id,
                    progress,
                )
                tool_result = await self._tool_runtime.execute(selected_tool)
                await self._remember_tool_result(task_id, tool_result)
                progress = _progress(iteration, self._settings, failure_count, plan)
                yield self._event(
                    "tool_execution_finished",
                    _runtime_status_for_tool(tool_result),
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.tool.finished",
                    details={
                        "tool_name": tool_result.tool_name,
                        "tool_status": tool_result.status.value,
                        "success": tool_result.success,
                        "message": tool_result.message,
                        "retryable": tool_result.retryable,
                        "error_code": tool_result.error_code,
                        "security_decision": _security_decision_from_tool_result(
                            tool_result
                        ),
                    },
                )

                if tool_result.status is ToolExecutionStatus.PAUSED:
                    confirmation_request = _confirmation_from_tool_result(tool_result)
                    result = await self._wait_for_confirmation(
                        task_id,
                        task,
                        progress,
                        plan,
                        tool_result.message,
                        confirmation_request=confirmation_request,
                    )
                    self.last_result = result
                    yield self._event(
                        "confirmation_required",
                        RuntimeStatus.WAITING_FOR_CONFIRMATION,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.confirmation.required",
                        details=_result_details(result),
                    )
                    return

                yield self._transition(
                    AgentState.EVALUATING,
                    "Evaluate progress after tool execution.",
                    task_id,
                    progress,
                )
                post_action_observation = await self._observation_engine.observe()
                previous_observation_signature = _observation_signature(
                    post_action_observation
                )
                await self._remember_observation(
                    task_id,
                    iteration,
                    post_action_observation,
                    phase="after_action",
                )
                yield self._event(
                    "post_action_observation_captured",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.observation.after_action",
                    details={
                        "url": post_action_observation.url,
                        "title": post_action_observation.title,
                        "summary": post_action_observation.summary,
                    },
                )
                blocker_decision = _page_blocker_decision(post_action_observation)
                if blocker_decision is not None:
                    await self._remember_event(
                        task_id,
                        f"page_blocker_{iteration}_after_action",
                        str(blocker_decision["memory_summary"]),
                    )
                    yield self._event(
                        "page_blocker_detected",
                        RuntimeStatus.FAILED
                        if blocker_decision["stop"]
                        else RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.page_blocker.detected",
                        details=blocker_decision,
                    )
                    if blocker_decision["stop"]:
                        result = await self._fail(
                            task_id,
                            task,
                            progress,
                            plan,
                            TaskTerminationReason.PAGE_BLOCKER,
                            str(blocker_decision["message"]),
                            failure_count,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_failed",
                            RuntimeStatus.FAILED,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.task.failed",
                            details={
                                **_result_details(result),
                                "page_blocker": blocker_decision,
                            },
                        )
                        return

                evaluation = await self._evaluator.evaluate_step(
                    StepEvaluationContext(
                        plan=plan,
                        tool_request=selected_tool,
                        tool_result=tool_result,
                        before_observation=observation,
                        after_observation=post_action_observation,
                        step=selected_plan_step,
                    )
                )
                await self._remember_reflection(task_id, iteration, evaluation)
                plan = _mark_plan_step(plan, selected_tool, evaluation)
                progress = _progress(iteration, self._settings, failure_count, plan)
                yield self._event(
                    "reflection_completed",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.reflection.completed",
                    details=_evaluation_details(evaluation),
                )

                if evaluation.recommended_action is RecoveryAction.REQUEST_CONFIRMATION:
                    result = await self._wait_for_confirmation(
                        task_id,
                        task,
                        progress,
                        plan,
                        evaluation.reflection_summary or tool_result.message,
                    )
                    self.last_result = result
                    yield self._event(
                        "confirmation_required",
                        RuntimeStatus.WAITING_FOR_CONFIRMATION,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.confirmation.required",
                        details=_result_details(result),
                    )
                    return

                if evaluation.outcome is StepOutcome.SUCCESS:
                    failure_count = 0
                elif evaluation.outcome is StepOutcome.FAILURE or evaluation.recommended_action in {
                    RecoveryAction.RETRY,
                    RecoveryAction.REPLAN,
                    RecoveryAction.STOP,
                }:
                    failure_count += 1

                if evaluation.recommended_action is RecoveryAction.STOP:
                    result = await self._fail(
                        task_id,
                        task,
                        _progress(iteration, self._settings, failure_count, plan),
                        plan,
                        TaskTerminationReason.TOOL_FAILURE,
                        evaluation.reflection_summary,
                        failure_count,
                    )
                    self.last_result = result
                    yield self._event(
                        "task_failed",
                        RuntimeStatus.FAILED,
                        task_id=task_id,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        message_key="runtime.task.failed",
                        details=_result_details(result),
                    )
                    return

                if evaluation.outcome is not StepOutcome.SUCCESS:
                    if failure_count >= self._settings.max_failures:
                        result = await self._fail(
                            task_id,
                            task,
                            _progress(iteration, self._settings, failure_count, plan),
                            plan,
                            TaskTerminationReason.MAX_FAILURES_EXCEEDED,
                            evaluation.reflection_summary,
                            failure_count,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_failed",
                            RuntimeStatus.FAILED,
                            task_id=task_id,
                            progress=_progress(iteration, self._settings, failure_count, plan),
                            message_key="runtime.task.failed",
                            details=_result_details(result),
                        )
                        return

                if evaluation.recommended_action in {
                    RecoveryAction.RETRY,
                    RecoveryAction.REPLAN,
                }:
                    plan, events = await self._handle_failure(
                        task=task,
                        task_id=task_id,
                        observation=post_action_observation,
                        plan=plan,
                        failure_count=failure_count,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        reason=evaluation.reflection_summary,
                    )
                    for event in events:
                        yield event
                    continue

                yield self._event(
                    "evaluation_completed",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.evaluation.completed",
                    details={
                        "recommended_action": evaluation.recommended_action.value,
                        "outcome": evaluation.outcome.value,
                    },
                )

                if self._cancel_requested:
                    event, result = await self._cancel(task_id, task, progress, plan)
                    self.last_result = result
                    yield event
                    return

            progress = _progress(
                self._settings.max_iterations,
                self._settings,
                failure_count,
                plan,
            )
            result = await self._fail(
                task_id,
                task,
                progress,
                plan,
                TaskTerminationReason.MAX_ITERATIONS_EXCEEDED,
                "Maximum iteration limit reached.",
                failure_count,
            )
            self.last_result = result
            yield self._event(
                "task_failed",
                RuntimeStatus.FAILED,
                task_id=task_id,
                progress=progress,
                message_key="runtime.task.failed",
                details=_result_details(result),
            )
            return
        except Exception as exc:
            logger.exception(
                "runtime_fatal_error",
                extra={
                    "event": "runtime_fatal_error",
                    "task_id": task_id,
                    "state": self._state.value,
                    "error_type": type(exc).__name__,
                },
            )
            progress = _progress(
                progress.iteration,
                self._settings,
                failure_count + 1,
                plan,
            )
            result = await self._fail(
                task_id,
                task,
                progress,
                plan,
                TaskTerminationReason.FATAL_ERROR,
                str(exc),
                failure_count + 1,
            )
            self.last_result = result
            yield self._event(
                "task_failed",
                RuntimeStatus.FAILED,
                task_id=task_id,
                progress=progress,
                message_key="runtime.task.failed",
                details=_result_details(result),
            )
            return

    async def run_to_result(self, task: UserTask) -> AgentTaskResult:
        """Run a task and return only the final result."""

        async for _event in self.run(task):
            pass
        if self.last_result is None:
            raise RuntimeError("Runtime ended without a task result.")
        return self.last_result

    async def _reason(
        self,
        task: UserTask,
        task_id: str,
        observation: PageObservation | None,
    ):
        return await self._reasoning_engine.reason(
            ReasoningContext(
                user_task=task.text,
                observation=observation,
                memory_summaries=self._memory_summaries(task_id),
                available_tools=self._tool_schemas,
                security_constraints=self._security_constraints,
                confirmation_constraints=self._confirmation_constraints,
                budget=self._budget,
            )
        )

    async def _needs_recovery(
        self,
        plan: ExecutionPlan | None,
        observation: PageObservation | None,
    ) -> bool:
        if self._evaluator is None or plan is None or observation is None:
            return False
        return await self._evaluator.needs_recovery(plan, observation)

    async def _handle_failure(
        self,
        *,
        task: UserTask,
        task_id: str,
        observation: PageObservation | None,
        plan: ExecutionPlan | None,
        failure_count: int,
        progress: AgentProgress,
        reason: str,
    ) -> tuple[ExecutionPlan | None, tuple[RuntimeEvent, ...]]:
        events: list[RuntimeEvent] = [
            self._transition(
                AgentState.RETRYING,
                f"Recover from failure: {reason}",
                task_id,
                progress,
            )
        ]
        await self._remember_event(
            task_id,
            f"failure_{failure_count}",
            f"Failure {failure_count}: {reason}",
        )
        revised_plan, revision_events = await self._revise_plan(
            task=task,
            task_id=task_id,
            observation=observation,
            plan=plan,
            progress=progress,
            reason=reason,
        )
        events.extend(revision_events)
        return revised_plan, tuple(events)

    async def _revise_plan(
        self,
        *,
        task: UserTask,
        task_id: str,
        observation: PageObservation | None,
        plan: ExecutionPlan | None,
        progress: AgentProgress,
        reason: str,
    ) -> tuple[ExecutionPlan | None, tuple[RuntimeEvent, ...]]:
        if plan is None:
            return plan, ()
        memory_summaries = self._memory_summaries(task_id)
        revised_plan = await self._planning_engine.revise_plan(
            plan,
            observation,
            reason=reason,
            memory_summaries=memory_summaries,
            available_tools=self._tool_schemas,
        )
        await self._remember_plan(task_id, revised_plan)
        events: list[RuntimeEvent] = []
        budget_event = self._context_budget_event(
            "planning_revision",
            self._planning_engine,
            task_id,
            progress,
        )
        if budget_event is not None:
            events.append(budget_event)
        events.append(
            self._event(
                "plan_revised",
                RuntimeStatus.RUNNING,
                task_id=task_id,
                progress=progress,
                message_key="runtime.plan.revised",
                details={
                    "summary": revised_plan.summary,
                    "reason": reason,
                    "steps": len(revised_plan.steps),
                    "current_plan_step": _first_pending_plan_step_summary(revised_plan),
                    "task": task.text,
                },
            )
        )
        return revised_plan, tuple(events)

    async def _complete(
        self,
        task_id: str,
        task: UserTask,
        progress: AgentProgress,
        plan: ExecutionPlan | None,
        answer: str,
    ) -> AgentTaskResult:
        result = AgentTaskResult(
            task_id=task_id,
            task=task,
            status=RuntimeStatus.COMPLETED,
            final_state=AgentState.COMPLETED,
            success=True,
            termination_reason=TaskTerminationReason.ANSWERED,
            message="Task completed.",
            answer=answer,
            iterations=progress.iteration,
            failures=progress.failure_count,
            plan=plan,
        )
        self._set_state(AgentState.COMPLETED, "Task produced a final answer.", task_id, progress)
        await self._remember_event(task_id, "task_completed", "Task completed successfully.")
        return result

    async def _wait_for_confirmation(
        self,
        task_id: str,
        task: UserTask,
        progress: AgentProgress,
        plan: ExecutionPlan | None,
        message: str,
        confirmation_request: Mapping[str, object] | None = None,
    ) -> AgentTaskResult:
        self._pending_confirmation = confirmation_request
        result = AgentTaskResult(
            task_id=task_id,
            task=task,
            status=RuntimeStatus.WAITING_FOR_CONFIRMATION,
            final_state=AgentState.WAITING_FOR_CONFIRMATION,
            success=False,
            termination_reason=TaskTerminationReason.WAITING_FOR_CONFIRMATION,
            message=message,
            iterations=progress.iteration,
            failures=progress.failure_count,
            plan=plan,
            confirmation_request=confirmation_request,
        )
        self._set_state(
            AgentState.WAITING_FOR_CONFIRMATION,
            "Runtime paused for user confirmation.",
            task_id,
            progress,
        )
        await self._remember_event(
            task_id,
            "waiting_for_confirmation",
            f"Runtime paused for confirmation: {message}",
        )
        return result

    async def _fail(
        self,
        task_id: str,
        task: UserTask,
        progress: AgentProgress,
        plan: ExecutionPlan | None,
        reason: TaskTerminationReason,
        message: str,
        failures: int,
    ) -> AgentTaskResult:
        result = AgentTaskResult(
            task_id=task_id,
            task=task,
            status=RuntimeStatus.FAILED,
            final_state=AgentState.FAILED,
            success=False,
            termination_reason=reason,
            message=message,
            iterations=progress.iteration,
            failures=failures,
            plan=plan,
        )
        self._set_state(AgentState.FAILED, f"Task failed: {message}", task_id, progress)
        await self._remember_event(task_id, "task_failed", f"Task failed: {message}")
        return result

    async def _cancel(
        self,
        task_id: str,
        task: UserTask,
        progress: AgentProgress,
        plan: ExecutionPlan | None,
    ) -> tuple[RuntimeEvent, AgentTaskResult]:
        result = AgentTaskResult(
            task_id=task_id,
            task=task,
            status=RuntimeStatus.CANCELLED,
            final_state=AgentState.CANCELLED,
            success=False,
            termination_reason=TaskTerminationReason.CANCELLED,
            message=self._cancel_reason,
            iterations=progress.iteration,
            failures=progress.failure_count,
            plan=plan,
        )
        self._set_state(AgentState.CANCELLED, self._cancel_reason, task_id, progress)
        await self._remember_event(task_id, "task_cancelled", self._cancel_reason)
        self._cancel_requested = False
        event = self._event(
            "task_cancelled",
            RuntimeStatus.CANCELLED,
            task_id=task_id,
            progress=progress,
            message_key="runtime.task.cancelled",
            details=_result_details(result),
        )
        return event, result

    def _transition(
        self,
        to_state: AgentState,
        reason: str,
        task_id: str,
        progress: AgentProgress,
    ) -> RuntimeEvent:
        from_state = self._state
        self._set_state(to_state, reason, task_id, progress)
        return self._event(
            "state_transition",
            _runtime_status_for_state(to_state),
            task_id=task_id,
            progress=progress,
            message_key=f"runtime.state.{to_state.value}",
            details={
                "from_state": from_state.value,
                "to_state": to_state.value,
                "reason": reason,
            },
        )

    def _set_state(
        self,
        to_state: AgentState,
        reason: str,
        task_id: str,
        progress: AgentProgress,
    ) -> None:
        from_state = self._state
        self._state = to_state
        logger.info(
            "state_transition",
            extra={
                "event": "state_transition",
                "task_id": task_id,
                "from_state": from_state.value,
                "to_state": to_state.value,
                "reason": reason,
                "iteration": progress.iteration,
                "failure_count": progress.failure_count,
            },
        )

    def _event(
        self,
        name: str,
        status: RuntimeStatus,
        *,
        task_id: str,
        progress: AgentProgress,
        message_key: str,
        details: Mapping[str, object] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            name=name,
            status=status,
            details={
                "task_id": task_id,
                "state": self._state.value,
                "message_key": message_key,
                "progress": progress.to_dict(),
                **dict(details or {}),
            },
        )

    def _context_budget_event(
        self,
        component: str,
        owner: object,
        task_id: str,
        progress: AgentProgress,
    ) -> RuntimeEvent | None:
        metrics = getattr(owner, "last_context_metrics", None)
        if metrics is None:
            return None
        to_dict = getattr(metrics, "to_dict", None)
        metric_details = dict(to_dict()) if callable(to_dict) else {}
        return self._event(
            "context_budget_applied",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            progress=progress,
            message_key="runtime.context.budget_applied",
            details={
                "component": component,
                "metrics": metric_details,
            },
        )

    async def _remember_task_goal(self, task_id: str, task: UserTask) -> None:
        await self._update_memory(
            MemoryRecord(
                key="user_goal",
                value={"goal": task.text},
                scope=task_id,
                layer=MemoryLayer.TASK,
                kind=MemoryRecordKind.USER_GOAL,
                importance=100,
                source="runtime",
            )
        )

    async def _remember_observation(
        self,
        task_id: str,
        iteration: int,
        observation: PageObservation,
        *,
        phase: str,
    ) -> None:
        await self._update_memory(
            MemoryRecord(
                key=f"observation_{iteration}_{phase}",
                value={
                    "url": observation.url,
                    "title": observation.title,
                    "summary": observation.summary,
                },
                scope=task_id,
                layer=MemoryLayer.WORKING,
                kind=MemoryRecordKind.OBSERVATION,
                importance=20,
                source="runtime",
            )
        )

    async def _remember_reflection(
        self,
        task_id: str,
        iteration: int,
        evaluation: StepEvaluation,
    ) -> None:
        await self._update_memory(
            MemoryRecord(
                key=f"reflection_{iteration}",
                value={
                    "summary": evaluation.reflection_summary,
                    "outcome": evaluation.outcome.value,
                    "recommended_action": evaluation.recommended_action.value,
                    "plan_validity": evaluation.plan_validity.value,
                    "page_changed": evaluation.page_changed,
                    "moved_forward": evaluation.moved_forward,
                    "reasons": list(evaluation.reasons[:3]),
                    "metrics": dict(evaluation.metrics.to_dict()),
                },
                scope=task_id,
                layer=MemoryLayer.EPISODIC,
                kind=MemoryRecordKind.SUMMARY,
                importance=30,
                source="execution_intelligence",
            )
        )

    async def _remember_plan(self, task_id: str, plan: ExecutionPlan) -> None:
        await self._update_memory(
            MemoryRecord(
                key="current_plan",
                value={
                    "summary": plan.summary or "Plan prepared.",
                    "step_count": len(plan.steps),
                },
                scope=task_id,
                layer=MemoryLayer.TASK,
                kind=MemoryRecordKind.SUMMARY,
                importance=50,
                source="runtime",
            )
        )

    async def _remember_tool_result(
        self,
        task_id: str,
        result: ToolExecutionResult,
    ) -> None:
        await self._remember_event(
            task_id,
            f"tool_{result.tool_name}_{result.finished_at.timestamp()}",
            f"Tool {result.tool_name} finished with {result.status.value}: {result.message}",
        )

    async def _remember_event(self, task_id: str, key: str, event: str) -> None:
        await self._update_memory(
            MemoryRecord(
                key=key,
                value={"event": event},
                scope=task_id,
                layer=MemoryLayer.EPISODIC,
                kind=MemoryRecordKind.EVENT,
                importance=10,
                source="runtime",
            )
        )

    async def _update_memory(self, record: MemoryRecord) -> None:
        try:
            await self._memory.update(record)
        except Exception as exc:
            logger.warning(
                "memory_update_failed",
                extra={
                    "event": "memory_update_failed",
                    "record_key": record.key,
                    "record_kind": record.kind.value,
                    "error_type": type(exc).__name__,
                },
            )

    def _memory_summaries(self, task_id: str) -> tuple[str, ...]:
        try:
            return tuple(
                self._memory.context_summaries(
                    task_id,
                    max_items=self._settings.max_memory_summaries,
                )
            )
        except Exception as exc:
            logger.warning(
                "memory_summary_failed",
                extra={
                    "event": "memory_summary_failed",
                    "task_id": task_id,
                    "error_type": type(exc).__name__,
                },
            )
            return ()


def _progress(
    iteration: int,
    settings: RuntimeSettings,
    failure_count: int,
    plan: ExecutionPlan | None,
) -> AgentProgress:
    steps = plan.steps if plan is not None else ()
    return AgentProgress(
        iteration=iteration,
        max_iterations=settings.max_iterations,
        failure_count=failure_count,
        max_failures=settings.max_failures,
        completed_steps=sum(1 for step in steps if step.status is PlanStepStatus.COMPLETED),
        total_steps=len(steps),
    )


def _mark_plan_step(
    plan: ExecutionPlan | None,
    request: ToolRequest,
    evaluation: StepEvaluation,
) -> ExecutionPlan | None:
    if plan is None:
        return None
    status = _step_status_for_evaluation(evaluation)
    steps: list[PlanStep] = []
    updated = False
    for step in plan.steps:
        if not updated and step.status is PlanStepStatus.PENDING and _matches_step(step, request):
            steps.append(replace(step, status=status))
            updated = True
            continue
        steps.append(step)
    if not updated:
        return plan
    return ExecutionPlan(
        task=plan.task,
        steps=steps,
        summary=plan.summary,
        warnings=plan.warnings,
        validation_errors=plan.validation_errors,
        source=plan.source,
        observation_url=plan.observation_url,
        observation_summary=plan.observation_summary,
        memory_summaries=plan.memory_summaries,
        is_fallback=plan.is_fallback,
        revision_reason=plan.revision_reason,
    )


def _find_plan_step(plan: ExecutionPlan | None, request: ToolRequest) -> PlanStep | None:
    if plan is None:
        return None
    return next(
        (
            step
            for step in plan.steps
            if step.status is PlanStepStatus.PENDING and _matches_step(step, request)
        ),
        None,
    )


def _first_pending_plan_step_summary(plan: ExecutionPlan | None) -> str | None:
    if plan is None:
        return None
    step = next(
        (step for step in plan.steps if step.status is PlanStepStatus.PENDING),
        None,
    )
    return _plan_step_summary(step)


def _plan_step_summary(step: PlanStep | None) -> str | None:
    if step is None:
        return None
    parts = [step.goal]
    if step.tool_name:
        parts.append(f"tool: {step.tool_name}")
    if step.requires_confirmation:
        parts.append("requires confirmation")
    if step.is_uncertain and step.uncertainty_reason:
        parts.append(f"uncertain: {step.uncertainty_reason}")
    return "; ".join(part for part in parts if part)


def _step_status_for_evaluation(evaluation: StepEvaluation) -> PlanStepStatus:
    if evaluation.outcome is StepOutcome.SUCCESS:
        return PlanStepStatus.COMPLETED
    if evaluation.outcome is StepOutcome.FAILURE:
        return PlanStepStatus.FAILED
    return PlanStepStatus.PENDING


def _matches_step(step: PlanStep, request: ToolRequest) -> bool:
    if step.tool_request is not None and step.tool_request.name == request.name:
        return True
    return step.tool_name == request.name


def _runtime_status_for_tool(result: ToolExecutionResult) -> RuntimeStatus:
    if result.status is ToolExecutionStatus.PAUSED:
        return RuntimeStatus.WAITING_FOR_CONFIRMATION
    if result.success:
        return RuntimeStatus.RUNNING
    return RuntimeStatus.FAILED


def _runtime_status_for_state(state: AgentState) -> RuntimeStatus:
    if state is AgentState.COMPLETED:
        return RuntimeStatus.COMPLETED
    if state is AgentState.CANCELLED:
        return RuntimeStatus.CANCELLED
    if state is AgentState.FAILED:
        return RuntimeStatus.FAILED
    if state is AgentState.WAITING_FOR_CONFIRMATION:
        return RuntimeStatus.WAITING_FOR_CONFIRMATION
    return RuntimeStatus.RUNNING


def _page_blocker_decision(observation: PageObservation) -> Mapping[str, object] | None:
    issue_codes = {issue.code for issue in observation.issues}
    if not issue_codes:
        return None

    issue_details = [_page_issue_details(issue) for issue in observation.issues]
    if PageIssueCode.CAPTCHA_BLOCKING_PAGE in issue_codes:
        return _blocker_decision(
            blocker_type="captcha_blocking_page",
            runtime_response="stop",
            message="Page requires CAPTCHA or human verification; runtime stops without bypassing it.",
            message_ru=(
                "Страница остановила автоматизацию проверкой CAPTCHA или подтверждением, что пользователь человек. "
                "Агент не обходит CAPTCHA и не продолжает без ручного действия."
            ),
            issues=issue_details,
            stop=True,
            requires_user_input=True,
            safe_dismiss_allowed=False,
        )
    if PageIssueCode.LOGIN_WALL in issue_codes:
        return _blocker_decision(
            blocker_type="login_wall",
            runtime_response="stop",
            message="Page requires manual login; runtime stops instead of automating credentials.",
            message_ru=(
                "Страница требует входа в аккаунт. Агент не автоматизирует логин и не просит вводить пароль в автоматическом режиме. "
                "Войдите вручную через persistent profile и запустите задачу снова."
            ),
            issues=issue_details,
            stop=True,
            requires_user_input=True,
            safe_dismiss_allowed=False,
        )
    if PageIssueCode.BLOCKED_PAGE in issue_codes:
        return _blocker_decision(
            blocker_type="blocked_page",
            runtime_response="stop",
            message="Page appears blocked or unavailable; runtime stops honestly.",
            message_ru=(
                "Страница выглядит заблокированной или недоступной для обычной автоматизации. "
                "Агент остановился честно и записал причину в отчет."
            ),
            issues=issue_details,
            stop=True,
            requires_user_input=True,
            safe_dismiss_allowed=False,
        )
    if PageIssueCode.REGION_PROMPT in issue_codes:
        return _blocker_decision(
            blocker_type="region_prompt",
            runtime_response="ask_user_when_unclear",
            message="Page asks for a region or location choice; runtime will not choose user preferences silently.",
            message_ru=(
                "Страница просит выбрать регион, город или доступ к местоположению. "
                "Агент не выбирает такие настройки молча; если окно мешает, выберите вариант вручную или уточните задачу."
            ),
            issues=issue_details,
            stop=False,
            requires_user_input=True,
            safe_dismiss_allowed=False,
        )
    if PageIssueCode.COOKIE_BANNER in issue_codes:
        return _blocker_decision(
            blocker_type="cookie_banner",
            runtime_response="safe_dismiss_if_generic_low_risk",
            message="Cookie or consent banner detected; only generic low-risk dismiss actions are acceptable.",
            message_ru=(
                "Обнаружен cookie/consent баннер. Агент может закрыть только очевидное низкорисковое окно; "
                "если действие неоднозначно, он должен остановиться или попросить подтверждение."
            ),
            issues=issue_details,
            stop=False,
            requires_user_input=False,
            safe_dismiss_allowed=True,
        )
    if PageIssueCode.MODAL_DIALOG in issue_codes:
        return _blocker_decision(
            blocker_type="modal_dialog",
            runtime_response="ask_user_when_unclear",
            message="Visible modal dialog detected; runtime records it and proceeds only through normal safe tools.",
            message_ru=(
                "На странице видно модальное окно. Агент учтет его как возможный блокер и не будет угадывать опасное действие."
            ),
            issues=issue_details,
            stop=False,
            requires_user_input=False,
            safe_dismiss_allowed=True,
        )
    if PageIssueCode.EMPTY_PAGE in issue_codes or (
        PageIssueCode.LOADING in issue_codes and not _has_useful_page_content(observation)
    ):
        return _blocker_decision(
            blocker_type="empty_or_loading_page",
            runtime_response="observe_or_replan",
            message="Page is empty or still loading; runtime records the issue before continuing.",
            message_ru=(
                "Страница пустая или еще загружается. Агент зафиксировал это в наблюдении и продолжит только если появится полезный контекст."
            ),
            issues=issue_details,
            stop=False,
            requires_user_input=False,
            safe_dismiss_allowed=False,
        )
    return None


def _observation_signature(observation: PageObservation) -> tuple[object, ...]:
    return (
        observation.url,
        observation.title,
        tuple(section.section_id for section in observation.sections),
        tuple(element.element_id for element in observation.interactive_elements),
        tuple((field.field_id, field.value_state) for field in observation.form_fields),
        tuple(issue.code.value for issue in observation.issues),
    )


def _has_useful_page_content(observation: PageObservation) -> bool:
    return bool(
        observation.sections
        or observation.interactive_elements
        or observation.form_fields
        or observation.dialogs
    )


def _blocker_decision(
    *,
    blocker_type: str,
    runtime_response: str,
    message: str,
    message_ru: str,
    issues: Sequence[Mapping[str, object]],
    stop: bool,
    requires_user_input: bool,
    safe_dismiss_allowed: bool,
) -> Mapping[str, object]:
    return {
        "blocker_type": blocker_type,
        "runtime_response": runtime_response,
        "message": message,
        "message_ru": message_ru,
        "issues": list(issues),
        "issue_codes": [str(issue.get("code")) for issue in issues],
        "stop": stop,
        "requires_user_input": requires_user_input,
        "safe_dismiss_allowed": safe_dismiss_allowed,
        "memory_summary": f"Page blocker detected: {blocker_type}; response={runtime_response}; stop={stop}.",
    }


def _page_issue_details(issue: PageIssue) -> Mapping[str, object]:
    return {
        "code": issue.code.value,
        "message": issue.message,
        "severity": issue.severity,
    }


def _result_details(result: AgentTaskResult) -> Mapping[str, object]:
    details = {
        "success": result.success,
        "termination_reason": result.termination_reason.value,
        "message": result.message,
        "message_ru": _user_message_ru_for_result(result),
        "answer": result.answer,
        "iterations": result.iterations,
        "failures": result.failures,
    }
    if result.confirmation_request is not None:
        details["confirmation_request"] = dict(result.confirmation_request)
    return details


def _provider_error_details(error: LlmProviderError | None) -> Mapping[str, object] | None:
    if error is None:
        return None
    return {
        "code": error.code.value,
        "retryable": error.retryable,
        "message": error.message,
    }


def _user_message_ru_for_result(result: AgentTaskResult) -> str:
    if result.termination_reason is TaskTerminationReason.ANSWERED:
        return "Задача завершена."
    if result.termination_reason is TaskTerminationReason.CANCELLED:
        return "Задача отменена пользователем."
    if result.termination_reason is TaskTerminationReason.WAITING_FOR_CONFIRMATION:
        if result.confirmation_request is not None:
            message_ru = result.confirmation_request.get("message_ru")
            if isinstance(message_ru, str) and message_ru.strip():
                return message_ru
        return (
            "Нужно подтверждение пользователя перед продолжением. Агент остановился и "
            "не будет выполнять следующее действие автоматически. Если вы хотите "
            "отменить действие, не подтверждайте его; можно уточнить задачу и "
            "запустить ее заново."
        )
    if result.termination_reason is TaskTerminationReason.REASONING_FAILURE:
        return (
            "Не удалось получить надежное решение от LLM-провайдера. "
            "Проверьте настройки провайдера, ключи доступа и сеть, затем повторите задачу."
        )
    if result.termination_reason is TaskTerminationReason.PAGE_BLOCKER:
        return (
            "На странице обнаружен блокер: CAPTCHA, login wall, региональный запрос, модальное окно или похожее препятствие. "
            "Агент не обходит такие проверки, не автоматизирует логин и записывает причину в отчет."
        )
    if result.termination_reason is TaskTerminationReason.MAX_ITERATIONS_EXCEEDED:
        return "Достигнут лимит итераций. Попробуйте сузить задачу или начать с более конкретной страницы."
    if result.termination_reason is TaskTerminationReason.MAX_FAILURES_EXCEEDED:
        return "Достигнут лимит повторных ошибок. Агент остановился, чтобы не выполнять одно и то же действие без пользы."
    if result.termination_reason is TaskTerminationReason.TOOL_FAILURE:
        return "Инструмент завершился ошибкой. Проверьте текущую страницу и повторите задачу после уточнения."
    return "Задача остановлена из-за внутренней ошибки. Проверьте debug-логи и повторите после исправления причины."


def _evaluation_details(evaluation: StepEvaluation) -> Mapping[str, object]:
    return {
        "outcome": evaluation.outcome.value,
        "recommended_action": evaluation.recommended_action.value,
        "plan_validity": evaluation.plan_validity.value,
        "page_changed": evaluation.page_changed,
        "moved_forward": evaluation.moved_forward,
        "confirmation_required": evaluation.confirmation_required,
        "reasons": list(evaluation.reasons),
        "alternative_actions": list(evaluation.alternative_actions),
        "metrics": dict(evaluation.metrics.to_dict()),
        "reflection_summary": evaluation.reflection_summary,
    }


def _confirmation_from_tool_result(
    result: ToolExecutionResult,
) -> Mapping[str, object] | None:
    confirmation = result.data.get("confirmation")
    if isinstance(confirmation, Mapping):
        return dict(confirmation)
    return None


def _security_decision_from_tool_result(
    result: ToolExecutionResult,
) -> Mapping[str, object]:
    if result.status is ToolExecutionStatus.VALIDATION_ERROR:
        return {
            "status": "not_run_validation",
            "reason": "Tool input validation failed before any browser action.",
        }
    security = result.data.get("security")
    if isinstance(security, Mapping):
        if result.status is ToolExecutionStatus.PAUSED:
            status = "pause"
        elif result.status is ToolExecutionStatus.BLOCKED:
            status = "block"
        else:
            status = "allow"
        return {
            "status": status,
            "risk": security.get("risk"),
            "reason": security.get("reason") or result.message,
            "audit_id": security.get("audit_id"),
        }
    if result.status is ToolExecutionStatus.PAUSED:
        return {"status": "pause", "reason": result.message}
    if result.status is ToolExecutionStatus.BLOCKED:
        return {"status": "block", "reason": result.message}
    return {
        "status": "allow",
        "reason": "Security policy allowed the tool before execution.",
    }


def _redact_tool_arguments(
    request: ToolRequest,
    schemas: Sequence[ToolSchema],
) -> Mapping[str, object]:
    schema = next((schema for schema in schemas if schema.name == request.name), None)
    sensitive_fields = (
        schema.input_schema.sensitive_field_names() if schema is not None else set()
    )
    redacted: dict[str, object] = {}
    for key, value in request.arguments.items():
        normalized = key.casefold()
        if key in sensitive_fields or any(
            hint in normalized
            for hint in ("password", "token", "secret", "cookie", "api_key", "value")
        ):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted
