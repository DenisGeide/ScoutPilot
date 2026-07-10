"""Autonomous Agent Runtime implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

from scout_pilot.intelligence.evaluator import ExecutionEvaluator
from scout_pilot.llm.reasoning import ReasoningEngine
from scout_pilot.llm.types import ReasoningContext, ReasoningStatus
from scout_pilot.memory.store import MemoryStore
from scout_pilot.models import (
    ExecutionPlan,
    MemoryLayer,
    MemoryRecord,
    MemoryRecordKind,
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
        self._evaluator = evaluator
        self._settings = settings or RuntimeSettings()
        self._security_constraints = tuple(security_constraints)
        self._confirmation_constraints = tuple(confirmation_constraints)
        self._budget = dict(budget or {})
        self._state = AgentState.IDLE
        self._cancel_requested = False
        self._cancel_reason = "Cancelled by user."
        self.last_result: AgentTaskResult | None = None

    @property
    def state(self) -> AgentState:
        return self._state

    def cancel(self, reason: str = "Cancelled by user.") -> None:
        """Request clean cancellation at the next runtime checkpoint."""

        self._cancel_requested = True
        self._cancel_reason = reason

    async def run(self, task: UserTask) -> AsyncIterator[RuntimeEvent]:
        """Run one task and stream deterministic runtime events."""

        task_id = uuid4().hex
        progress = _progress(0, self._settings, 0, None)
        observation: PageObservation | None = None
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
                await self._remember_observation(task_id, iteration, observation)
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
                    memory_summaries = self._memory.context_summaries(
                        task_id,
                        max_items=self._settings.max_memory_summaries,
                    )
                    plan = await self._planning_engine.create_plan(
                        task,
                        observation,
                        memory_summaries=memory_summaries,
                        available_tools=self._tool_schemas,
                    )
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
                yield self._event(
                    "reasoning_completed",
                    RuntimeStatus.RUNNING,
                    task_id=task_id,
                    progress=progress,
                    message_key="runtime.reasoning.completed",
                    details={
                        "status": reasoning.status.value,
                        "message": reasoning.message,
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

                yield self._transition(
                    AgentState.EXECUTING,
                    f"Execute selected tool {reasoning.selected_tool.name}.",
                    task_id,
                    progress,
                )
                tool_result = await self._tool_runtime.execute(reasoning.selected_tool)
                await self._remember_tool_result(task_id, tool_result)
                plan = _mark_plan_step(plan, reasoning.selected_tool, tool_result)
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
                    },
                )

                if tool_result.status is ToolExecutionStatus.PAUSED:
                    result = await self._wait_for_confirmation(
                        task_id,
                        task,
                        progress,
                        plan,
                        tool_result.message,
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

                if not tool_result.success:
                    failure_count += 1
                    if (
                        not tool_result.retryable
                        or failure_count >= self._settings.max_failures
                    ):
                        result = await self._fail(
                            task_id,
                            task,
                            _progress(iteration, self._settings, failure_count, plan),
                            plan,
                            TaskTerminationReason.TOOL_FAILURE
                            if not tool_result.retryable
                            else TaskTerminationReason.MAX_FAILURES_EXCEEDED,
                            tool_result.message,
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
                    plan, events = await self._handle_failure(
                        task=task,
                        task_id=task_id,
                        observation=observation,
                        plan=plan,
                        failure_count=failure_count,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        reason=tool_result.message,
                    )
                    for event in events:
                        yield event
                    continue

                failure_count = 0
                yield self._transition(
                    AgentState.EVALUATING,
                    "Evaluate progress after successful tool execution.",
                    task_id,
                    progress,
                )
                if await self._needs_recovery(plan, observation):
                    plan, events = await self._revise_plan(
                        task=task,
                        task_id=task_id,
                        observation=observation,
                        plan=plan,
                        progress=progress,
                        reason="Execution evaluator requested recovery.",
                    )
                    for event in events:
                        yield event
                else:
                    yield self._event(
                        "evaluation_completed",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.evaluation.completed",
                        details={"needs_recovery": False},
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
                memory_summaries=self._memory.context_summaries(
                    task_id,
                    max_items=self._settings.max_memory_summaries,
                ),
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
        memory_summaries = self._memory.context_summaries(
            task_id,
            max_items=self._settings.max_memory_summaries,
        )
        revised_plan = await self._planning_engine.revise_plan(
            plan,
            observation,
            reason=reason,
            memory_summaries=memory_summaries,
            available_tools=self._tool_schemas,
        )
        await self._remember_plan(task_id, revised_plan)
        return revised_plan, (
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
                    "task": task.text,
                },
            ),
        )

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
    ) -> AgentTaskResult:
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

    async def _remember_task_goal(self, task_id: str, task: UserTask) -> None:
        await self._memory.update(
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
    ) -> None:
        await self._memory.update(
            MemoryRecord(
                key=f"observation_{iteration}",
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

    async def _remember_plan(self, task_id: str, plan: ExecutionPlan) -> None:
        await self._memory.update(
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
        await self._memory.update(
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
    result: ToolExecutionResult,
) -> ExecutionPlan | None:
    if plan is None:
        return None
    status = PlanStepStatus.COMPLETED if result.success else PlanStepStatus.FAILED
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


def _result_details(result: AgentTaskResult) -> Mapping[str, object]:
    return {
        "success": result.success,
        "termination_reason": result.termination_reason.value,
        "message": result.message,
        "answer": result.answer,
        "iterations": result.iterations,
        "failures": result.failures,
    }
