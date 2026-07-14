"""Autonomous Agent Runtime implementation."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from hashlib import sha256
from time import monotonic
from typing import Protocol
from urllib.parse import urlparse
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
from scout_pilot.llm.types import (
    LlmProviderError,
    ReasoningContext,
    ReasoningResult,
    ReasoningStatus,
)
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
from scout_pilot.navigation import SemanticNavigationResolver
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

_UPPER_BOUND_CLAUSE_PATTERN = re.compile(
    r"(?ix)"
    r"(?:\b(?:с\s+указанн\w+\s+)?(?:зарплат\w*|доход\w*|цен\w*|salary|pay|price)\s*)?"
    r"\b(?:до|не\s+выше|не\s+более|максимум|up\s+to|at\s+most|under|maximum)\s*"
    r"(?:[$€£₽]\s*)?(?P<number>\d[\d\s.,]*)"
    r"(?:\s*(?:руб\w*|доллар\w*|usd|eur|rub|₽|\$|€))?"
)
_LOWER_BOUND_CLAUSE_PATTERN = re.compile(
    r"(?ix)"
    r"(?:\b(?:с\s+указанн\w+\s+)?(?:зарплат\w*|доход\w*|цен\w*|salary|pay|price)\s*)?"
    r"\b(?:от|не\s+ниже|не\s+менее|минимум|from|at\s+least|over|minimum)\s*"
    r"(?:[$€£₽]\s*)?(?P<number>\d[\d\s.,]*)"
    r"(?:\s*(?:руб\w*|доллар\w*|usd|eur|rub|₽|\$|€))?"
)
_BOUND_SEARCH_TERMS_PATTERN = re.compile(
    r"(?i)\b(?:с\s+указанн\w+\s+)?(?:зарплат\w*|доход\w*|цен\w*|salary|pay|price)\b"
)


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
        initial_memory_summaries: Sequence[str] = (),
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
        self._initial_memory_summaries = tuple(
            summary.strip() for summary in initial_memory_summaries if summary.strip()
        )
        self._budget = dict(budget or {})
        self._state = AgentState.IDLE
        self._cancel_requested = False
        self._cancel_reason = "Cancelled by user."
        self._pending_confirmation: Mapping[str, object] | None = None
        self.last_result: AgentTaskResult | None = None
        self.last_observed_resource_urls: tuple[str, ...] = ()
        self.last_visited_target_urls: tuple[str, ...] = ()
        self.last_repeated_target_preventions = 0

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
        visited_target_urls: set[str] = set()
        observed_resource_urls: set[str] = set()
        matched_resource_urls: set[str] = set()
        prior_resource_urls = _resource_urls_from_summaries(self._initial_memory_summaries)
        attempted_tool_requests: set[tuple[tuple[object, ...], str]] = set()
        resolved_tool_followups: dict[tuple[tuple[object, ...], str], ToolRequest] = {}
        search_reformulation_counts: Counter[str] = Counter()
        pending_fast_followup: ToolRequest | None = None
        pending_fast_followup_event = ""
        repeated_target_count = 0
        requested_resource_count = _requested_distinct_resource_count(task.text)
        auto_finalize_resource_count = (
            None if _task_requires_qualified_resources(task.text) else requested_resource_count
        )
        resource_probe_target = _qualified_resource_probe_target(
            task.text,
            requested_resource_count,
        )
        memory_only_followup = _task_is_memory_only_followup(
            task.text,
            prior_resource_urls,
        )
        started_at = monotonic()
        plan: ExecutionPlan | None = None
        failure_count = 0
        self._state = AgentState.IDLE
        self.last_result = None
        self.last_observed_resource_urls = ()
        self.last_visited_target_urls = ()
        self.last_repeated_target_preventions = 0

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
                if monotonic() - started_at >= self._settings.max_elapsed_seconds:
                    result = await self._partial_result_after_limit(
                        task=task,
                        task_id=task_id,
                        observation=observation,
                        visited_target_urls=visited_target_urls,
                        progress=progress,
                        plan=plan,
                        reason=(
                            "Maximum live task duration reached before the full goal was completed."
                        ),
                    )
                    self.last_result = result
                    yield self._event(
                        "task_partial_result",
                        RuntimeStatus.COMPLETED,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.task.partial_result",
                        details={
                            **_result_details(result),
                            "completion_trigger": "max_elapsed_seconds",
                            "max_elapsed_seconds": self._settings.max_elapsed_seconds,
                        },
                    )
                    return

                transition = self._transition(
                    AgentState.OBSERVING,
                    "Start iteration by capturing semantic page state.",
                    task_id,
                    progress,
                )
                yield transition
                observation = await self._observation_engine.observe()
                if _can_auto_dismiss_modal(observation):
                    dismiss_request = ToolRequest(
                        name="browser.press_key",
                        arguments={"key": "Escape"},
                    )
                    yield self._event(
                        "modal_dismiss_started",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.modal.dismiss_started",
                        details={
                            "tool_name": dismiss_request.name,
                            "next_action": "dismiss_low_risk_modal",
                        },
                    )
                    dismiss_result = await self._tool_runtime.execute(dismiss_request)
                    await self._remember_tool_result(task_id, dismiss_result)
                    refreshed_observation = await self._observation_engine.observe()
                    dismissed = not _has_page_issue(
                        refreshed_observation,
                        PageIssueCode.MODAL_DIALOG,
                    )
                    yield self._event(
                        "modal_dismiss_finished",
                        RuntimeStatus.RUNNING
                        if dismiss_result.success and dismissed
                        else RuntimeStatus.FAILED,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.modal.dismiss_finished",
                        details={
                            "tool_name": dismiss_result.tool_name,
                            "tool_status": dismiss_result.status.value,
                            "success": dismiss_result.success,
                            "dismissed": dismissed,
                            "message": dismiss_result.message,
                            "next_action": ("continue_task" if dismissed else "reason_about_modal"),
                        },
                    )
                    if dismiss_result.success and dismissed:
                        observation = refreshed_observation
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
                if _resource_observation_has_evidence(observation):
                    observed_resource_urls.add(str(observation.url))
                    self.last_observed_resource_urls = tuple(sorted(observed_resource_urls))
                    if _resource_observation_matches_explicit_evidence(task.text, observation):
                        matched_resource_urls.add(str(observation.url))
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
                if memory_only_followup:
                    yield self._transition(
                        AgentState.REASONING,
                        "Answer an analytical follow-up from bounded conversation memory.",
                        task_id,
                        progress,
                    )
                    answer = await self._finalize_answer(
                        task=task,
                        task_id=task_id,
                        observation=observation,
                        visited_target_urls=prior_resource_urls,
                        reason=(
                            "This is a read-only comparison or ranking of results collected in "
                            "the previous conversation turn. Answer from memory without browsing."
                        ),
                    )
                    budget_event = self._context_budget_event(
                        "memory_followup_finalization",
                        self._reasoning_engine,
                        task_id,
                        progress,
                    )
                    if budget_event is not None:
                        yield budget_event
                    result = await self._complete(task_id, task, progress, plan, answer)
                    self.last_result = result
                    yield self._event(
                        "task_completed",
                        RuntimeStatus.COMPLETED,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.task.completed",
                        details={
                            **_result_details(result),
                            "completion_trigger": "memory_only_followup",
                            **_run_evidence_details(
                                observed_resource_urls,
                                visited_target_urls,
                                self.last_repeated_target_preventions,
                            ),
                        },
                    )
                    return
                blocker_decision = _page_blocker_decision(observation)
                if blocker_decision is not None:
                    await self._remember_event(
                        task_id,
                        f"page_blocker_{iteration}_before_action",
                        str(blocker_decision["memory_summary"]),
                    )
                    yield self._event(
                        "page_blocker_detected",
                        RuntimeStatus.FAILED if blocker_decision["stop"] else RuntimeStatus.RUNNING,
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

                completed_resource_count = _dominant_visited_resource_count(observed_resource_urls)
                matched_resource_count = _dominant_visited_resource_count(matched_resource_urls)
                matched_count_reached = (
                    resource_probe_target is not None
                    and requested_resource_count is not None
                    and matched_resource_count >= requested_resource_count
                )
                qualified_probe_reached = (
                    resource_probe_target is not None
                    and completed_resource_count >= resource_probe_target
                )
                if (
                    (
                        auto_finalize_resource_count is not None
                        and completed_resource_count >= auto_finalize_resource_count
                    )
                    or matched_count_reached
                    or qualified_probe_reached
                ):
                    yield self._transition(
                        AgentState.REASONING,
                        "Requested distinct resource count reached; finalize without more tools.",
                        task_id,
                        progress,
                    )
                    answer = await self._finalize_answer(
                        task=task,
                        task_id=task_id,
                        observation=observation,
                        visited_target_urls=visited_target_urls,
                        reason=(
                            f"Collected {completed_resource_count} distinct resource pages; "
                            f"{matched_resource_count} contain the explicit evidence requested by "
                            "the task. Return the best verified result now."
                        ),
                    )
                    budget_event = self._context_budget_event(
                        "finalization",
                        self._reasoning_engine,
                        task_id,
                        progress,
                    )
                    if budget_event is not None:
                        yield budget_event
                    result = await self._complete(
                        task_id,
                        task,
                        progress,
                        plan,
                        answer,
                    )
                    self.last_result = result
                    yield self._event(
                        "task_completed",
                        RuntimeStatus.COMPLETED,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.task.completed",
                        details={
                            **_result_details(result),
                            "completion_trigger": (
                                "matched_resource_count_reached"
                                if matched_count_reached
                                else "qualified_resource_probe_reached"
                                if qualified_probe_reached
                                else "requested_resource_count_reached"
                            ),
                            "completed_resource_count": completed_resource_count,
                            "matched_resource_count": matched_resource_count,
                            **_run_evidence_details(
                                observed_resource_urls,
                                visited_target_urls,
                                self.last_repeated_target_preventions,
                            ),
                        },
                    )
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

                if pending_fast_followup is None:
                    collection_followup = _deterministic_resource_collection_tool(
                        task_text=task.text,
                        requested_resource_count=requested_resource_count,
                        resource_probe_target=resource_probe_target,
                        observation=observation,
                        observed_resource_urls=observed_resource_urls,
                        matched_resource_urls=matched_resource_urls,
                        visited_target_urls=visited_target_urls,
                        prior_resource_urls=prior_resource_urls,
                        available_tool_names={schema.name for schema in self._tool_schemas},
                    )
                    if collection_followup is not None:
                        pending_fast_followup = collection_followup
                        pending_fast_followup_event = "resource_collection_followup_selected"

                if pending_fast_followup is not None:
                    yield self._transition(
                        AgentState.REASONING,
                        "Execution Intelligence selected a deterministic browser follow-up.",
                        task_id,
                        progress,
                    )
                    reasoning = ReasoningResult.tool_selected(
                        pending_fast_followup,
                        "Execute the deterministic follow-up selected from browser state.",
                    )
                    selected_followup_event = (
                        pending_fast_followup_event or "deterministic_followup_selected"
                    )
                    pending_fast_followup = None
                    pending_fast_followup_event = ""
                    yield self._event(
                        selected_followup_event,
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.tool.semantic_recovery_selected",
                        details={"next_action": "open_unvisited_search_result"},
                    )
                else:
                    yield self._transition(
                        AgentState.REASONING,
                        "Ask provider-neutral Reasoning Engine for next decision.",
                        task_id,
                        progress,
                    )
                    reasoning_observation = _observation_with_visited_targets_last(
                        observation,
                        visited_target_urls,
                    )
                    reasoning = await self._reason(
                        task,
                        task_id,
                        reasoning_observation,
                        visited_target_urls,
                    )
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
                        "provider_error": _provider_error_details(reasoning.provider_error),
                    },
                )

                if reasoning.status is ReasoningStatus.ANSWER:
                    answer = reasoning.answer or reasoning.message
                    recovery_tool = _incomplete_qualified_answer_recovery_tool(
                        task_text=task.text,
                        answer=answer,
                        requested_resource_count=requested_resource_count,
                        resource_probe_target=resource_probe_target,
                        observation=observation,
                        observed_resource_urls=observed_resource_urls,
                        visited_target_urls=visited_target_urls,
                        available_tool_names={schema.name for schema in self._tool_schemas},
                    )
                    if recovery_tool is not None:
                        reasoning = ReasoningResult.tool_selected(
                            recovery_tool,
                            "Continue collecting distinct resources before finalizing a filtered result.",
                        )
                        yield self._event(
                            "incomplete_answer_collection_continued",
                            RuntimeStatus.RUNNING,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.answer.collection_continued",
                            details={
                                "requested_resource_count": requested_resource_count,
                                "answer_resource_count": _answer_observed_resource_count(
                                    answer,
                                    observed_resource_urls,
                                ),
                                "observed_resource_count": _dominant_visited_resource_count(
                                    observed_resource_urls
                                ),
                                "probe_target": resource_probe_target,
                                "next_action": recovery_tool.name,
                            },
                        )
                    else:
                        result = await self._complete(
                            task_id,
                            task,
                            progress,
                            plan,
                            answer,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_completed",
                            RuntimeStatus.COMPLETED,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.task.completed",
                            details={
                                **_result_details(result),
                                **_run_evidence_details(
                                    observed_resource_urls,
                                    visited_target_urls,
                                    self.last_repeated_target_preventions,
                                ),
                            },
                        )
                        return

                if reasoning.status is ReasoningStatus.NEEDS_CONFIRMATION:
                    await self._remember_event(
                        task_id,
                        f"ungrounded_confirmation_{iteration}",
                        (
                            "Reasoning requested confirmation without a concrete tool request. "
                            "Only Security Policy may pause an executable action."
                        ),
                    )
                    reasoning = ReasoningResult.failure(
                        (
                            "Reasoning requested confirmation without selecting a concrete tool. "
                            "Select the tool so deterministic Security Policy can decide."
                        )
                    )

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
                        partial_progress = _progress(
                            iteration,
                            self._settings,
                            failure_count,
                            plan,
                        )
                        result = await self._partial_result_after_limit(
                            task=task,
                            task_id=task_id,
                            observation=observation,
                            visited_target_urls=visited_target_urls,
                            progress=partial_progress,
                            plan=plan,
                            reason=f"Reasoning failure limit reached: {reasoning.message}",
                        )
                        self.last_result = result
                        yield self._event(
                            "task_partial_result",
                            RuntimeStatus.COMPLETED,
                            task_id=task_id,
                            progress=partial_progress,
                            message_key="runtime.task.partial_result",
                            details=_result_details(result),
                        )
                        return
                    continue

                if reasoning.selected_tool is None:
                    failure_count += 1
                    continue

                selected_tool = reasoning.selected_tool
                resolution_request_key = (
                    _observation_signature(observation),
                    _tool_request_signature(selected_tool),
                )
                resolved_followup = resolved_tool_followups.get(resolution_request_key)
                if resolved_followup is not None:
                    selected_tool = resolved_followup
                    yield self._event(
                        "resolved_target_reused",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.tool.resolved_target_reused",
                        details={
                            "tool_name": selected_tool.name,
                            "next_action": "execute_previously_resolved_target",
                        },
                    )
                selected_tool, upper_bound_removed = _without_upper_bound_search_filter(
                    task.text,
                    observation,
                    selected_tool,
                )
                if upper_bound_removed:
                    yield self._event(
                        "search_upper_bound_removed",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.tool.search_upper_bound_removed",
                        details={
                            "tool_name": selected_tool.name,
                            "next_action": "search_by_subject_and_post_filter_upper_bound",
                        },
                    )
                search_scope = _search_fill_scope(observation, selected_tool)
                if (
                    search_scope is not None
                    and search_reformulation_counts[search_scope]
                    >= self._settings.max_search_reformulations
                ):
                    unvisited_result = _first_unvisited_resource_tool(
                        observation,
                        visited_target_urls,
                        preferred_shape=_preferred_resource_shape(
                            observed_resource_urls,
                            prior_resource_urls,
                        ),
                    )
                    if unvisited_result is not None:
                        selected_tool = unvisited_result
                        search_scope = None
                        yield self._event(
                            "search_reformulation_redirected",
                            RuntimeStatus.RUNNING,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.tool.search_reformulation_redirected",
                            details={
                                "tool_name": selected_tool.name,
                                "next_action": "open_unvisited_search_result",
                            },
                        )
                selected_plan_step = _find_plan_step(plan, selected_tool)
                selected_target_url = _target_url_for_tool(observation, selected_tool)
                preferred_resource_shape = _preferred_resource_shape(
                    observed_resource_urls,
                    prior_resource_urls,
                ) or _dominant_interactive_resource_shape(observation)
                selected_resource_shape = (
                    _url_resource_shape(selected_target_url) if selected_target_url else None
                )
                if (
                    requested_resource_count is not None
                    and preferred_resource_shape is not None
                    and selected_resource_shape is not None
                    and selected_resource_shape != preferred_resource_shape
                ):
                    scoped_alternative = _first_unvisited_resource_tool(
                        observation,
                        visited_target_urls,
                        preferred_shape=preferred_resource_shape,
                    )
                    if scoped_alternative is not None:
                        selected_tool = scoped_alternative
                        selected_target_url = _target_url_for_tool(
                            observation,
                            selected_tool,
                        )
                        selected_plan_step = _find_plan_step(plan, selected_tool)
                        yield self._event(
                            "off_scope_resource_redirected",
                            RuntimeStatus.RUNNING,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.tool.off_scope_resource_redirected",
                            details={
                                "tool_name": selected_tool.name,
                                "next_action": "open_same_resource_type",
                            },
                        )
                if selected_target_url and _target_was_visited(
                    selected_target_url,
                    visited_target_urls,
                ):
                    alternative_tool = _alternative_unvisited_target_tool(
                        observation,
                        selected_tool,
                        selected_target_url,
                        visited_target_urls,
                    )
                    if alternative_tool is not None:
                        alternative_target_url = _target_url_for_tool(
                            observation,
                            alternative_tool,
                        )
                        yield self._event(
                            "repeated_target_remapped",
                            RuntimeStatus.RUNNING,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.tool.repeated_target_remapped",
                            details={
                                "original_target_url": selected_target_url,
                                "target_url": alternative_target_url,
                                "tool_name": alternative_tool.name,
                                "next_action": "open_unvisited_equivalent_target",
                            },
                        )
                        self.last_repeated_target_preventions += 1
                        selected_tool = alternative_tool
                        selected_target_url = alternative_target_url
                        selected_plan_step = _find_plan_step(plan, selected_tool)
                request_attempt_key = (
                    _observation_signature(observation),
                    _tool_request_signature(selected_tool),
                )
                if request_attempt_key in attempted_tool_requests and not (
                    selected_target_url
                    and _target_was_visited(selected_target_url, visited_target_urls)
                ):
                    failure_count += 1
                    message = (
                        "The exact tool request was already attempted on the unchanged semantic "
                        "page. Re-observe, resolve a more specific target, choose a discovered "
                        "URL, or use a different tool."
                    )
                    await self._remember_event(
                        task_id,
                        f"repeated_tool_request_blocked_{iteration}",
                        f"{message} Tool: {selected_tool.name}.",
                    )
                    yield self._event(
                        "repeated_tool_request_blocked",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        message_key="runtime.tool.repeated_request_blocked",
                        details={
                            "tool_name": selected_tool.name,
                            "next_action": "choose_different_tool_or_more_specific_target",
                        },
                    )
                    if failure_count >= self._settings.max_failures:
                        partial_progress = _progress(
                            iteration,
                            self._settings,
                            failure_count,
                            plan,
                        )
                        result = await self._partial_result_after_limit(
                            task=task,
                            task_id=task_id,
                            observation=observation,
                            visited_target_urls=visited_target_urls,
                            progress=partial_progress,
                            plan=plan,
                            reason=message,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_partial_result",
                            RuntimeStatus.COMPLETED,
                            task_id=task_id,
                            progress=partial_progress,
                            message_key="runtime.task.partial_result",
                            details=_result_details(result),
                        )
                        return
                    continue
                attempted_tool_requests.add(request_attempt_key)
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
                if selected_target_url and _target_was_visited(
                    selected_target_url,
                    visited_target_urls,
                ):
                    repeated_target_count += 1
                    self.last_repeated_target_preventions += 1
                    message = (
                        "Repeated navigation to an already visited target URL was blocked. "
                        "Choose a different unvisited semantic result or answer from memory."
                    )
                    await self._remember_event(
                        task_id,
                        f"repeated_target_blocked_{iteration}",
                        f"{message} URL: {selected_target_url}",
                    )
                    yield self._event(
                        "repeated_target_blocked",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=progress,
                        message_key="runtime.tool.repeated_target_blocked",
                        details={
                            "tool_name": selected_tool.name,
                            "target_url": selected_target_url,
                            "next_action": "choose_unvisited_target_or_answer",
                        },
                    )
                    if repeated_target_count >= self._settings.max_repeated_targets:
                        result = await self._partial_result_after_limit(
                            task=task,
                            task_id=task_id,
                            observation=observation,
                            visited_target_urls=visited_target_urls,
                            progress=progress,
                            plan=plan,
                            reason=message,
                        )
                        self.last_result = result
                        yield self._event(
                            "task_partial_result",
                            RuntimeStatus.COMPLETED,
                            task_id=task_id,
                            progress=progress,
                            message_key="runtime.task.partial_result",
                            details=_result_details(result),
                        )
                        return
                    continue
                yield self._transition(
                    AgentState.EXECUTING,
                    f"Execute selected tool {selected_tool.name}.",
                    task_id,
                    progress,
                )
                tool_result = await self._tool_runtime.execute(selected_tool)
                if tool_result.success and search_scope is not None:
                    search_reformulation_counts[search_scope] += 1
                    if any(schema.name == "browser.press_key" for schema in self._tool_schemas):
                        pending_fast_followup = ToolRequest(
                            name="browser.press_key",
                            arguments={"key": "Enter"},
                        )
                        pending_fast_followup_event = "search_submit_selected"
                resolved_followup = _resolved_target_followup(selected_tool, tool_result)
                if resolved_followup is not None:
                    resolved_tool_followups[request_attempt_key] = resolved_followup
                    pending_fast_followup = resolved_followup
                    pending_fast_followup_event = "resolved_target_followup_selected"
                if tool_result.success and selected_target_url:
                    visited_target_urls.add(selected_target_url)
                    self.last_visited_target_urls = tuple(sorted(visited_target_urls))
                    repeated_target_count = 0
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
                        "security_decision": _security_decision_from_tool_result(tool_result),
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
                previous_observation_signature = _observation_signature(post_action_observation)
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
                        RuntimeStatus.FAILED if blocker_decision["stop"] else RuntimeStatus.RUNNING,
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

                semantic_recovery = _semantic_failure_recovery_tool(
                    task_text=task.text,
                    request=selected_tool,
                    result=tool_result,
                    observation=post_action_observation,
                    visited_target_urls=visited_target_urls,
                )
                if semantic_recovery is not None:
                    pending_fast_followup = semantic_recovery
                    pending_fast_followup_event = "semantic_recovery_selected"
                    failure_count += 1
                    await self._remember_event(
                        task_id,
                        f"semantic_recovery_{iteration}",
                        (
                            "A semantic target was ambiguous or unavailable. Continue with the "
                            "next unvisited resource URL already present in the observation."
                        ),
                    )
                    yield self._event(
                        "semantic_recovery_scheduled",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        message_key="runtime.tool.semantic_recovery_scheduled",
                        details={
                            "failed_tool": selected_tool.name,
                            "recovery_tool": semantic_recovery.name,
                            "next_action": "open_unvisited_search_result",
                        },
                    )
                    yield self._event(
                        "evaluation_completed",
                        RuntimeStatus.RUNNING,
                        task_id=task_id,
                        progress=_progress(iteration, self._settings, failure_count, plan),
                        message_key="runtime.evaluation.completed",
                        details={
                            "recommended_action": "alternative_action",
                            "outcome": evaluation.outcome.value,
                        },
                    )
                    continue

                if evaluation.outcome is StepOutcome.SUCCESS:
                    failure_count = 0
                elif evaluation.outcome is StepOutcome.FAILURE or evaluation.recommended_action in {
                    RecoveryAction.RETRY,
                    RecoveryAction.REPLAN,
                    RecoveryAction.REQUEST_CONFIRMATION,
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
                        partial_progress = _progress(
                            iteration,
                            self._settings,
                            failure_count,
                            plan,
                        )
                        result = await self._partial_result_after_limit(
                            task=task,
                            task_id=task_id,
                            observation=post_action_observation,
                            visited_target_urls=visited_target_urls,
                            progress=partial_progress,
                            plan=plan,
                            reason=(
                                "Repeated tool/recovery failure limit reached: "
                                f"{evaluation.reflection_summary}"
                            ),
                        )
                        self.last_result = result
                        yield self._event(
                            "task_partial_result",
                            RuntimeStatus.COMPLETED,
                            task_id=task_id,
                            progress=partial_progress,
                            message_key="runtime.task.partial_result",
                            details=_result_details(result),
                        )
                        return

                if evaluation.recommended_action in {
                    RecoveryAction.RETRY,
                    RecoveryAction.REPLAN,
                    RecoveryAction.REQUEST_CONFIRMATION,
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
            result = await self._partial_result_after_limit(
                task=task,
                task_id=task_id,
                observation=observation,
                visited_target_urls=visited_target_urls,
                progress=progress,
                plan=plan,
                reason="Maximum autonomous step limit reached.",
            )
            self.last_result = result
            yield self._event(
                "task_partial_result",
                RuntimeStatus.COMPLETED,
                task_id=task_id,
                progress=progress,
                message_key="runtime.task.partial_result",
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
                f"Unexpected runtime failure ({type(exc).__name__}).",
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
        visited_target_urls: set[str],
    ):
        return await self._reasoning_engine.reason(
            ReasoningContext(
                user_task=task.text,
                observation=observation,
                memory_summaries=self._memory_summaries(task_id),
                available_tools=self._tool_schemas,
                security_constraints=self._security_constraints,
                confirmation_constraints=self._confirmation_constraints,
                visited_target_urls=tuple(sorted(visited_target_urls))[-20:],
                budget=self._budget,
            )
        )

    async def _finalize_answer(
        self,
        *,
        task: UserTask,
        task_id: str,
        observation: PageObservation | None,
        visited_target_urls: set[str],
        reason: str,
    ) -> str:
        finalization_task = (
            f"{task.text}\n\nRuntime completion checkpoint: {reason} "
            "Return the final or best-effort partial answer now. Use only collected evidence. "
            "Do not request or call any tool. Include exact known URLs and clearly identify "
            "facts that could not be verified."
        )
        reasoning = await self._reasoning_engine.reason(
            ReasoningContext(
                user_task=finalization_task,
                observation=observation,
                memory_summaries=self._memory_summaries(task_id),
                available_tools=(),
                security_constraints=self._security_constraints,
                confirmation_constraints=self._confirmation_constraints,
                visited_target_urls=tuple(sorted(visited_target_urls))[-20:],
                final_answer_only=True,
                budget=self._budget,
            )
        )
        if reasoning.status is ReasoningStatus.ANSWER and reasoning.answer:
            return reasoning.answer
        return self._deterministic_partial_answer(task_id, reason)

    def _deterministic_partial_answer(self, task_id: str, reason: str) -> str:
        evidence = [
            summary
            for summary in self._memory_summaries(task_id)
            if "http://" in summary or "https://" in summary
        ]
        if not evidence:
            return f"Не удалось сформировать полный ответ до защитной остановки. Причина: {reason}"
        lines = "\n".join(f"- {item}" for item in evidence[:8])
        return (
            "Полный проход не завершен, но агент сохранил уже проверенные данные:\n"
            f"{lines}\n\nПричина досрочного завершения: {reason}"
        )

    async def _partial_result_after_limit(
        self,
        *,
        task: UserTask,
        task_id: str,
        observation: PageObservation | None,
        visited_target_urls: set[str],
        progress: AgentProgress,
        plan: ExecutionPlan | None,
        reason: str,
    ) -> AgentTaskResult:
        answer = await self._finalize_answer(
            task=task,
            task_id=task_id,
            observation=observation,
            visited_target_urls=visited_target_urls,
            reason=reason,
        )
        return await self._complete_partial(
            task_id,
            task,
            progress,
            plan,
            answer,
            reason,
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

    async def _complete_partial(
        self,
        task_id: str,
        task: UserTask,
        progress: AgentProgress,
        plan: ExecutionPlan | None,
        answer: str,
        reason: str,
    ) -> AgentTaskResult:
        result = AgentTaskResult(
            task_id=task_id,
            task=task,
            status=RuntimeStatus.COMPLETED,
            final_state=AgentState.COMPLETED,
            success=False,
            termination_reason=TaskTerminationReason.PARTIAL_RESULT,
            message=reason,
            answer=answer,
            iterations=progress.iteration,
            failures=progress.failure_count,
            plan=plan,
        )
        self._set_state(
            AgentState.COMPLETED,
            "Runtime returned collected evidence after a protective limit.",
            task_id,
            progress,
        )
        await self._remember_event(
            task_id,
            "task_partial_result",
            "Runtime returned a best-effort partial result after a protective limit.",
        )
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
        resource_summaries = _resource_observation_summaries(observation)
        if resource_summaries and observation.url is not None:
            resource_key = sha256(
                repr(_target_identity(observation.url)).encode("utf-8")
            ).hexdigest()[:16]
            for part_index, resource_summary in enumerate(resource_summaries, start=1):
                await self._update_memory(
                    MemoryRecord(
                        key=f"resource_evidence_{resource_key}_{part_index}",
                        value={"summary": resource_summary},
                        scope=task_id,
                        layer=MemoryLayer.TASK,
                        kind=MemoryRecordKind.SUMMARY,
                        importance=70,
                        source="runtime_semantic_observation",
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
        summary = f"Tool {result.tool_name} finished with {result.status.value}: {result.message}"
        resolution = result.data.get("resolution")
        if isinstance(resolution, Mapping):
            selected = resolution.get("selected")
            if isinstance(selected, Mapping):
                name = str(selected.get("name") or "").strip()[:160]
                target_url = str(selected.get("target_url") or "").strip()[:500]
                parsed = urlparse(target_url)
                if name and parsed.scheme in {"http", "https"} and parsed.netloc:
                    summary = f"{summary} Resolved {name} to {target_url}."
        await self._remember_event(
            task_id,
            f"tool_{result.tool_name}_{result.finished_at.timestamp()}",
            summary,
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
            current = tuple(
                self._memory.context_summaries(
                    task_id,
                    max_items=self._settings.max_memory_summaries,
                )
            )
            initial = tuple(dict.fromkeys(self._initial_memory_summaries))[-3:]
            current_limit = max(self._settings.max_memory_summaries - len(initial), 0)
            recent_current = current[-current_limit:] if current_limit else ()
            return tuple(dict.fromkeys((*initial, *recent_current)))
        except Exception as exc:
            logger.warning(
                "memory_summary_failed",
                extra={
                    "event": "memory_summary_failed",
                    "task_id": task_id,
                    "error_type": type(exc).__name__,
                },
            )
            return self._initial_memory_summaries[-self._settings.max_memory_summaries :]


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
    if PageIssueCode.OBSERVATION_ERROR in issue_codes:
        return _blocker_decision(
            blocker_type="browser_observation_error",
            runtime_response="restart_browser",
            message="Browser observation is unavailable; runtime stops before reasoning over missing page state.",
            message_ru=(
                "Связь с браузером потеряна или страницу не удалось прочитать. "
                "Агент остановил текущую задачу без действий; перед следующей задачей браузер можно перезапустить."
            ),
            issues=issue_details,
            stop=True,
            requires_user_input=False,
            safe_dismiss_allowed=False,
        )
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
            runtime_response="safe_dismiss_then_reason_if_still_visible",
            message="Visible modal dialog remains after a safe dismiss attempt; runtime records it before reasoning.",
            message_ru=(
                "На странице осталось модальное окно. Агент уже попробовал безопасно закрыть его; "
                "дальше он учтет окно как блокер и не будет угадывать опасное действие."
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


def _target_url_for_tool(
    observation: PageObservation | None,
    request: ToolRequest,
) -> str | None:
    if request.name == "browser.navigate":
        value = request.arguments.get("url")
        return value.strip() if isinstance(value, str) and value.strip() else None
    if observation is None:
        return None

    if request.name == "browser.click_by_intent":
        target = request.arguments.get("target")
        if not isinstance(target, str) or not target.strip():
            return None
        resolver = SemanticNavigationResolver()
        requested_role = _optional_tool_argument(request, "role")
        context = _optional_tool_argument(request, "context")
        resolution = resolver.resolve_click(
            observation,
            target=target,
            role=requested_role,
            context=context,
        )
        if not resolution.is_resolved or resolution.selected is None:
            return None
        target_url = (resolution.selected.target_url or "").strip()
        if not target_url and requested_role != "link":
            linked_target = resolver.resolve_click(
                observation,
                target=target,
                role="link",
                context=context,
            )
            if linked_target.is_resolved and linked_target.selected is not None:
                target_url = (linked_target.selected.target_url or "").strip()
        return target_url or None

    if request.name != "browser.click":
        return None

    element_id = request.arguments.get("element_id")
    if not isinstance(element_id, str) or not element_id:
        return None
    element = next(
        (
            candidate
            for candidate in observation.interactive_elements
            if candidate.element_id == element_id
        ),
        None,
    )
    if element is None or not element.target_url:
        return None
    return element.target_url.strip() or None


def _resolved_target_followup(
    request: ToolRequest,
    result: ToolExecutionResult,
) -> ToolRequest | None:
    """Turn a resolved click intent into a concrete, security-checked action."""

    if request.name != "browser.resolve_target" or not result.success:
        return None
    if request.arguments.get("kind") != "click":
        return None

    resolution = result.data.get("resolution")
    if not isinstance(resolution, Mapping) or resolution.get("status") != "resolved":
        return None
    selected = resolution.get("selected")
    if not isinstance(selected, Mapping):
        return None

    target_url = selected.get("target_url")
    if isinstance(target_url, str):
        target_url = target_url.strip()
        parsed = urlparse(target_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return ToolRequest(
                name="browser.navigate",
                arguments={"url": target_url},
            )

    element_id = selected.get("id")
    if isinstance(element_id, str) and element_id.strip():
        return ToolRequest(
            name="browser.click",
            arguments={"element_id": element_id.strip()},
        )
    return None


def _optional_tool_argument(request: ToolRequest, name: str) -> str | None:
    value = request.arguments.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _observation_with_visited_targets_last(
    observation: PageObservation,
    visited_target_urls: set[str],
) -> PageObservation:
    if not visited_target_urls:
        return observation

    unvisited = []
    visited = []
    for element in observation.interactive_elements:
        target_url = (element.target_url or "").strip()
        if not target_url or not _target_was_visited(target_url, visited_target_urls):
            unvisited.append(element)
            continue
        name = element.accessible_name or element.visible_text or "visited link"
        visited.append(
            replace(
                element,
                accessible_name=f"[already visited] {name}",
            )
        )
    if not visited:
        return observation
    return PageObservation(
        url=observation.url,
        title=observation.title,
        summary=observation.summary,
        elements=observation.elements,
        metadata=observation.metadata,
        sections=observation.sections,
        interactive_elements=(*unvisited, *visited),
        form_fields=observation.form_fields,
        focused_element=observation.focused_element,
        dialogs=observation.dialogs,
        issues=observation.issues,
        limits={**observation.limits, "visited_targets_marked": len(visited)},
    )


def _search_fill_scope(
    observation: PageObservation,
    request: ToolRequest,
) -> str | None:
    is_search_fill = False
    if request.name == "browser.fill_by_label":
        label = request.arguments.get("label")
        is_search_fill = isinstance(label, str) and bool(
            re.search(
                r"(?i)(?:\bsearch\b|\bfind\b|\bquery\b|\bпоиск\b|\bискать\b|\bнайти\b)",
                label,
            )
        )
    elif request.name == "browser.fill":
        element_id = request.arguments.get("element_id")
        if isinstance(element_id, str):
            field = next(
                (
                    candidate
                    for candidate in observation.form_fields
                    if candidate.field_id == element_id
                ),
                None,
            )
            is_search_fill = bool(
                field is not None and (field.role == "searchbox" or field.input_type == "search")
            )
    if not is_search_fill:
        return None

    parsed = urlparse(observation.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"


def _without_upper_bound_search_filter(
    task_text: str,
    observation: PageObservation,
    request: ToolRequest,
) -> tuple[ToolRequest, bool]:
    if _search_fill_scope(observation, request) is None:
        return request, False

    value = request.arguments.get("value")
    if not isinstance(value, str) or not value.strip():
        return request, False
    task_bounds = {
        _digits_only(match.group("number"))
        for match in _UPPER_BOUND_CLAUSE_PATTERN.finditer(task_text)
    }
    task_bounds.discard("")
    if not task_bounds:
        return request, False
    value_digits = _digits_only(value)
    matching_bounds = {bound for bound in task_bounds if bound in value_digits}
    if not matching_bounds:
        return request, False

    cleaned = _UPPER_BOUND_CLAUSE_PATTERN.sub(" ", value)
    for bound in matching_bounds:
        spaced_number = r"\s*".join(re.escape(digit) for digit in bound)
        cleaned = re.sub(spaced_number, " ", cleaned)
    cleaned = _BOUND_SEARCH_TERMS_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")
    if not cleaned or cleaned == value.strip():
        return request, False

    return (
        ToolRequest(
            name=request.name,
            arguments={**request.arguments, "value": cleaned},
        ),
        True,
    )


def _digits_only(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _resource_urls_from_summaries(summaries: Sequence[str]) -> set[str]:
    urls = {
        raw_url.rstrip(".,;:)]}")
        for summary in summaries
        for raw_url in re.findall(r"https?://[^\s<>\"']+", summary)
    }
    return {url for url in urls if _url_resource_shape(url) is not None}


def _task_is_memory_only_followup(task_text: str, prior_resource_urls: set[str]) -> bool:
    if not prior_resource_urls:
        return False
    normalized = " ".join(task_text.casefold().split())
    analysis_terms = (
        "compare",
        "rank",
        "evaluate",
        "score",
        "summarize",
        "best option",
        "сравн",
        "оцени",
        "рейтинг",
        "выбери лучш",
        "лучший вариант",
        "резюм",
    )
    browsing_terms = (
        "find ",
        "search",
        "open ",
        "navigate",
        "visit ",
        "read each",
        "click",
        "найд",
        "ищи",
        "поиск",
        "открой",
        "перейди",
        "прочитай кажд",
        "нажм",
    )
    return any(term in normalized for term in analysis_terms) and not any(
        term in normalized for term in browsing_terms
    )


def _preferred_resource_shape(
    observed_resource_urls: set[str],
    prior_resource_urls: set[str],
) -> tuple[str, str, str] | None:
    return _dominant_resource_shape(observed_resource_urls) or _dominant_resource_shape(
        prior_resource_urls
    )


def _first_unvisited_resource_tool(
    observation: PageObservation,
    visited_target_urls: set[str],
    *,
    preferred_shape: tuple[str, str, str] | None = None,
) -> ToolRequest | None:
    candidate = next(
        (
            element
            for element in observation.interactive_elements
            if element.role == "link"
            and element.target_url
            and _url_resource_shape(element.target_url) is not None
            and (
                preferred_shape is None
                or _url_resource_shape(element.target_url) == preferred_shape
            )
            and not _target_was_visited(element.target_url, visited_target_urls)
            and _target_identity(element.target_url) != _target_identity(observation.url)
        ),
        None,
    )
    if candidate is None or candidate.target_url is None:
        return None
    return ToolRequest(
        name="browser.navigate",
        arguments={"url": candidate.target_url},
    )


def _deterministic_resource_collection_tool(
    *,
    task_text: str,
    requested_resource_count: int | None,
    resource_probe_target: int | None,
    observation: PageObservation,
    observed_resource_urls: set[str],
    matched_resource_urls: set[str],
    visited_target_urls: set[str],
    prior_resource_urls: set[str],
    available_tool_names: set[str],
) -> ToolRequest | None:
    if requested_resource_count is None or not observed_resource_urls:
        return None
    completed_count = _dominant_visited_resource_count(observed_resource_urls)
    matched_count = _dominant_visited_resource_count(matched_resource_urls)
    target_count = resource_probe_target or requested_resource_count
    if matched_count >= requested_resource_count or completed_count >= target_count:
        return None

    preferred_shape = _preferred_resource_shape(observed_resource_urls, prior_resource_urls)
    known_urls = {*visited_target_urls, *observed_resource_urls, *prior_resource_urls}
    if (
        preferred_shape is not None
        and _url_resource_shape(str(observation.url or "")) == preferred_shape
        and "browser.back" in available_tool_names
    ):
        return ToolRequest(name="browser.back", arguments={})
    unvisited = _first_unvisited_resource_tool(
        observation,
        known_urls,
        preferred_shape=preferred_shape,
    )
    if unvisited is not None:
        return unvisited
    return None


def _incomplete_qualified_answer_recovery_tool(
    *,
    task_text: str,
    answer: str,
    requested_resource_count: int | None,
    resource_probe_target: int | None,
    observation: PageObservation,
    observed_resource_urls: set[str],
    visited_target_urls: set[str],
    available_tool_names: set[str],
) -> ToolRequest | None:
    if (
        requested_resource_count is None
        or resource_probe_target is None
        or not _task_requires_qualified_resources(task_text)
    ):
        return None
    if _answer_observed_resource_count(answer, observed_resource_urls) >= requested_resource_count:
        return None
    if _dominant_visited_resource_count(observed_resource_urls) >= resource_probe_target:
        return None

    known_resource_urls = {*visited_target_urls, *observed_resource_urls}
    unvisited = _first_unvisited_resource_tool(
        observation,
        known_resource_urls,
        preferred_shape=_dominant_resource_shape(observed_resource_urls),
    )
    if unvisited is not None:
        return unvisited
    if (
        observation.url
        and _url_resource_shape(observation.url) is not None
        and "browser.back" in available_tool_names
    ):
        return ToolRequest(name="browser.back", arguments={})
    return None


def _answer_observed_resource_count(
    answer: str,
    observed_resource_urls: set[str],
) -> int:
    observed_identities = {
        _target_identity(url)
        for url in observed_resource_urls
        if _url_resource_shape(url) is not None
    }
    answer_identities = {
        _target_identity(url.rstrip(".,;:)]}"))
        for url in re.findall(r"https?://[^\s<>\"']+", answer)
    }
    return len(observed_identities & answer_identities)


def _semantic_failure_recovery_tool(
    *,
    task_text: str,
    request: ToolRequest,
    result: ToolExecutionResult,
    observation: PageObservation,
    visited_target_urls: set[str],
) -> ToolRequest | None:
    """Recover failed semantic selection from a visible list of concrete resources."""

    if result.success or _requested_distinct_resource_count(task_text) is None:
        return None
    if request.name not in {
        "browser.resolve_target",
        "browser.click_by_intent",
        "browser.click",
    }:
        return None

    candidates = [
        element
        for element in observation.interactive_elements
        if element.role == "link"
        and element.target_url
        and _url_resource_shape(element.target_url) is not None
        and not _target_was_visited(element.target_url, visited_target_urls)
        and _target_identity(element.target_url) != _target_identity(observation.url)
    ]
    shape_counts = Counter(
        shape
        for shape in (_url_resource_shape(element.target_url or "") for element in candidates)
        if shape is not None
    )
    if not shape_counts:
        return None
    dominant_shape, candidate_count = shape_counts.most_common(1)[0]
    if candidate_count < 2:
        return None
    candidate = next(
        element
        for element in candidates
        if _url_resource_shape(element.target_url or "") == dominant_shape
    )
    return ToolRequest(
        name="browser.navigate",
        arguments={"url": str(candidate.target_url)},
    )


def _alternative_unvisited_target_tool(
    observation: PageObservation,
    request: ToolRequest,
    selected_target_url: str,
    visited_target_urls: set[str],
) -> ToolRequest | None:
    selected_shape = _url_resource_shape(selected_target_url)
    if selected_shape is None:
        return None
    candidate = next(
        (
            element
            for element in observation.interactive_elements
            if element.role == "link"
            and element.target_url
            and not _target_was_visited(element.target_url, visited_target_urls)
            and _url_resource_shape(element.target_url) == selected_shape
        ),
        None,
    )
    if candidate is None or candidate.target_url is None:
        return None
    if request.name == "browser.click":
        return ToolRequest(
            name="browser.click",
            arguments={"element_id": candidate.element_id},
        )
    if request.name in {"browser.navigate", "browser.click_by_intent"}:
        return ToolRequest(
            name="browser.navigate",
            arguments={"url": candidate.target_url},
        )
    return None


def _url_resource_shape(url: str) -> tuple[str, str, str] | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/") or "/"
    shaped_path = re.sub(
        r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        "{id}",
        path,
    )
    shaped_path = re.sub(r"\d{3,}", "{id}", shaped_path)
    if shaped_path == path:
        return None
    return parsed.scheme.casefold(), parsed.netloc.casefold(), shaped_path.casefold()


def _dominant_interactive_resource_shape(
    observation: PageObservation,
) -> tuple[str, str, str] | None:
    shapes = [
        shape
        for shape in (
            _url_resource_shape(element.target_url or "")
            for element in observation.interactive_elements
            if element.role == "link"
        )
        if shape is not None
    ]
    if not shapes:
        return None
    return Counter(shapes).most_common(1)[0][0]


def _tool_request_signature(request: ToolRequest) -> str:
    """Hash a tool request so repeated actions can be blocked without logging values."""

    normalized_arguments = tuple(
        sorted(
            (str(key), _stable_signature_value(value)) for key, value in request.arguments.items()
        )
    )
    payload = repr((request.name, normalized_arguments)).encode("utf-8")
    return sha256(payload).hexdigest()


def _stable_signature_value(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            sorted((str(key), _stable_signature_value(item)) for key, item in value.items())
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_stable_signature_value(item) for item in value)
    return value


def _target_was_visited(url: str, visited_target_urls: set[str]) -> bool:
    identity = _target_identity(url)
    return any(_target_identity(visited_url) == identity for visited_url in visited_target_urls)


def _target_identity(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    return (
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        (parsed.path.rstrip("/") or "/").casefold(),
    )


_RESOURCE_COUNT_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "две": 2,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}
_RESOURCE_COUNT_NOUN_TERMS = (
    "different",
    "distinct",
    "pages",
    "items",
    "results",
    "links",
    "jobs",
    "vacancies",
    "products",
    "messages",
    "emails",
    "restaurants",
    "разн",
    "страниц",
    "результат",
    "ссыл",
    "ваканс",
    "товар",
    "письм",
    "сообщен",
    "ресторан",
    "вариант",
)


def _requested_distinct_resource_count(task_text: str) -> int | None:
    normalized = " ".join(task_text.casefold().split())
    action_terms = (
        "find",
        "open",
        "read",
        "compare",
        "select",
        "найд",
        "открой",
        "прочита",
        "сравн",
        "выбер",
    )
    if not any(term in normalized for term in action_terms):
        return None
    for match in re.finditer(r"\b\d{1,2}\b", normalized):
        value = int(match.group())
        if 2 <= value <= 20 and _count_followed_by_resource_term(normalized, match.end()):
            return value
    for word, value in _RESOURCE_COUNT_WORDS.items():
        match = re.search(rf"(?<!\w){re.escape(word)}(?!\w)", normalized)
        if match is not None and _count_followed_by_resource_term(normalized, match.end()):
            return value
    return None


def _task_has_hard_numeric_filter(task_text: str) -> bool:
    normalized = " ".join(task_text.casefold().split())
    return bool(
        re.search(
            r"(?:до|от|не\s+выше|не\s+ниже|не\s+более|не\s+менее|"
            r"up\s+to|at\s+most|at\s+least|under|over|more\s+than|less\s+than)"
            r"\s*(?:[$€£₽]\s*)?\d",
            normalized,
        )
    )


_QUALIFIED_RESOURCE_TERMS = (
    "salary",
    "pay range",
    "experience",
    "work format",
    "technology",
    "technologies",
    "requirements",
    "employer",
    "exclude",
    "must have",
    "зарплат",
    "доход",
    "опыт",
    "формат работ",
    "технолог",
    "требован",
    "работодател",
    "исключ",
    "обязательн",
    "явно указан",
)


def _task_requires_qualified_resources(task_text: str) -> bool:
    normalized = " ".join(task_text.casefold().split())
    return _task_has_hard_numeric_filter(task_text) or any(
        term in normalized for term in _QUALIFIED_RESOURCE_TERMS
    )


def _qualified_resource_probe_target(
    task_text: str,
    requested_resource_count: int | None,
) -> int | None:
    if requested_resource_count is None or not _task_requires_qualified_resources(task_text):
        return None
    return min(requested_resource_count + 2, 8)


def _count_followed_by_resource_term(text: str, count_end: int) -> bool:
    tail = text[count_end : count_end + 48]
    return any(term in tail for term in _RESOURCE_COUNT_NOUN_TERMS)


def _dominant_resource_shape(
    resource_urls: set[str],
) -> tuple[str, str, str] | None:
    shapes = [
        shape for shape in (_url_resource_shape(url) for url in resource_urls) if shape is not None
    ]
    if not shapes:
        return None
    return Counter(shapes).most_common(1)[0][0]


def _dominant_visited_resource_count(visited_target_urls: set[str]) -> int:
    dominant_shape = _dominant_resource_shape(visited_target_urls)
    if dominant_shape is None:
        return 0
    return sum(_url_resource_shape(url) == dominant_shape for url in visited_target_urls)


_RESOURCE_EVIDENCE_TERMS = (
    "requirements",
    "qualifications",
    "responsibilities",
    "skills",
    "experience",
    "technology",
    "stack",
    "требован",
    "квалификац",
    "обязанност",
    "задач",
    "навык",
    "опыт",
    "технолог",
    "стек",
)


def _resource_observation_summaries(observation: PageObservation) -> tuple[str, ...]:
    if observation.url is None or _url_resource_shape(observation.url) is None:
        return ()
    overview_parts = [
        part.strip()
        for part in (observation.title or "", observation.url, observation.summary)
        if part and part.strip()
    ]
    overview = " | ".join(dict.fromkeys(overview_parts))[:560]

    sections: list[str] = []
    seen: set[str] = set()
    for section in observation.sections:
        text = " ".join(section.text.split()).strip()
        if len(text) < 20 or text.casefold() in seen:
            continue
        sections.append(text)
        seen.add(text.casefold())

    ranked = sorted(
        enumerate(sections),
        key=lambda item: (-_resource_evidence_score(item[1]), item[0]),
    )
    selected_indexes = {0} if sections else set()
    for index, _text in ranked:
        selected_indexes.add(index)
        if len(selected_indexes) >= 2:
            break

    details = [
        f"{(observation.title or 'Resource')[:120]} | Content: {sections[index][:420]}"
        for index in sorted(selected_indexes)
    ]
    return tuple(part for part in (overview, *details) if part)


def _resource_evidence_score(text: str) -> int:
    normalized = text.casefold()
    return sum(1 for term in _RESOURCE_EVIDENCE_TERMS if term in normalized)


def _resource_observation_has_evidence(observation: PageObservation) -> bool:
    if observation.url is None or _url_resource_shape(observation.url) is None:
        return False
    meaningful_text = " ".join(
        " ".join(section.text.split())
        for section in observation.sections
        if len(" ".join(section.text.split())) >= 20
    )
    return len(meaningful_text) >= 20


_COMPENSATION_TASK_TERMS = (
    "salary",
    "pay range",
    "compensation",
    "income",
    "зарплат",
    "доход",
    "оплат",
)
_COMPENSATION_VALUE_PATTERN = re.compile(
    r"(?:\d[\d\s.,]{1,14}\s*(?:₽|руб(?:\.|\b|л)|rub\b|usd\b|eur\b|"
    r"доллар|евро|[$€£])|"
    r"(?:₽|[$€£]|rub\b|usd\b|eur\b)\s*\d[\d\s.,]{1,14})",
    re.IGNORECASE,
)
_COMPENSATION_MISSING_PATTERN = re.compile(
    r"(?:salary|pay|compensation|зарплата|доход)\s*(?:is\s*)?"
    r"(?:not specified|not provided|не указан|не указана)",
    re.IGNORECASE,
)
_MONEY_CURRENCY_PATTERN = (
    r"(?P<currency>₽|р(?:уб(?:\.|\b|ля|лей)?|\.)|rub\b|usd\b|eur\b|"
    r"доллар(?:а|ов)?|евро|[$€£])"
)
_MONEY_RANGE_PATTERN = re.compile(
    rf"(?P<lower>\d[\d\s.,]{{0,14}})\s*(?:[-–—]|до)\s*"
    rf"(?P<upper>\d[\d\s.,]{{0,14}})\s*(?P<multiplier>тыс\.?)?\s*"
    rf"{_MONEY_CURRENCY_PATTERN}",
    re.IGNORECASE,
)
_MONEY_DIRECTION_PATTERN = re.compile(
    rf"(?P<direction>от|до|from|up\s+to|at\s+least|at\s+most|minimum|maximum)\s*"
    rf"(?P<amount>\d[\d\s.,]{{0,14}})\s*(?P<multiplier>тыс\.?)?\s*"
    rf"{_MONEY_CURRENCY_PATTERN}",
    re.IGNORECASE,
)
_MONEY_EXACT_PATTERN = re.compile(
    rf"(?P<amount>\d[\d\s.,]{{0,14}})\s*(?P<multiplier>тыс\.?)?\s*"
    rf"{_MONEY_CURRENCY_PATTERN}",
    re.IGNORECASE,
)


def _resource_observation_matches_explicit_evidence(
    task_text: str,
    observation: PageObservation,
) -> bool:
    """Count a resource as matching only when required visible facts are present."""

    normalized_task = " ".join(task_text.casefold().split())
    if not any(term in normalized_task for term in _COMPENSATION_TASK_TERMS):
        return True

    parts = [observation.title or "", observation.summary]
    parts.extend(
        f"{section.heading or ''} {section.text}"
        for section in observation.sections
        if section.role.casefold() not in {"banner", "contentinfo", "footer", "navigation"}
    )
    visible_content = " ".join(" ".join(part.split()) for part in parts if part)
    if _COMPENSATION_MISSING_PATTERN.search(visible_content):
        return False
    if _COMPENSATION_VALUE_PATTERN.search(visible_content) is None:
        return False

    lower_bound, upper_bound, task_currency = _task_compensation_constraints(task_text)
    if lower_bound is None and upper_bound is None:
        return True
    return any(
        (task_currency is None or currency == task_currency)
        and (lower_bound is None or offer_lower is not None and offer_lower >= lower_bound)
        and (upper_bound is None or offer_upper is not None and offer_upper <= upper_bound)
        for offer_lower, offer_upper, currency in _visible_compensation_ranges(visible_content)
    )


def _task_compensation_constraints(
    task_text: str,
) -> tuple[int | None, int | None, str | None]:
    upper_values = [
        _money_number(match.group("number"))
        for match in _UPPER_BOUND_CLAUSE_PATTERN.finditer(task_text)
        if _is_compensation_clause(match.group(0))
    ]
    lower_values = [
        _money_number(match.group("number"))
        for match in _LOWER_BOUND_CLAUSE_PATTERN.finditer(task_text)
        if _is_compensation_clause(match.group(0))
    ]
    upper_values = [value for value in upper_values if value is not None]
    lower_values = [value for value in lower_values if value is not None]
    return (
        max(lower_values) if lower_values else None,
        min(upper_values) if upper_values else None,
        _money_currency(task_text),
    )


def _is_compensation_clause(text: str) -> bool:
    normalized = text.casefold()
    return any(term in normalized for term in _COMPENSATION_TASK_TERMS) or (
        _money_currency(text) is not None
    )


def _visible_compensation_ranges(
    text: str,
) -> tuple[tuple[int | None, int | None, str], ...]:
    ranges: list[tuple[int | None, int | None, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in _MONEY_RANGE_PATTERN.finditer(text):
        multiplier = match.group("multiplier")
        lower = _money_number(match.group("lower"), multiplier)
        upper = _money_number(match.group("upper"), multiplier)
        currency = _money_currency(match.group("currency"))
        if lower is not None and upper is not None and currency is not None:
            ranges.append((min(lower, upper), max(lower, upper), currency))
            occupied.append(match.span())

    for match in _MONEY_DIRECTION_PATTERN.finditer(text):
        if _span_overlaps(match.span(), occupied):
            continue
        amount = _money_number(match.group("amount"), match.group("multiplier"))
        currency = _money_currency(match.group("currency"))
        if amount is None or currency is None:
            continue
        direction = match.group("direction").casefold()
        if direction in {"от", "from", "at least", "minimum"}:
            ranges.append((amount, None, currency))
        else:
            ranges.append((None, amount, currency))
        occupied.append(match.span())

    for match in _MONEY_EXACT_PATTERN.finditer(text):
        if _span_overlaps(match.span(), occupied):
            continue
        amount = _money_number(match.group("amount"), match.group("multiplier"))
        currency = _money_currency(match.group("currency"))
        if amount is not None and currency is not None:
            ranges.append((amount, amount, currency))
    return tuple(ranges)


def _money_number(value: str, multiplier: str | None = None) -> int | None:
    digits = _digits_only(value)
    if not digits:
        return None
    amount = int(digits)
    if multiplier and amount < 10_000:
        amount *= 1_000
    return amount


def _money_currency(text: str) -> str | None:
    normalized = text.casefold()
    if re.search(r"(?:₽|руб|\brub\b|р(?:\.|\b))", normalized):
        return "RUB"
    if re.search(r"(?:\$|\busd\b|доллар)", normalized):
        return "USD"
    if re.search(r"(?:€|\beur\b|евро)", normalized):
        return "EUR"
    if "£" in normalized:
        return "GBP"
    return None


def _span_overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in occupied)


def _has_useful_page_content(observation: PageObservation) -> bool:
    return bool(
        observation.sections
        or observation.interactive_elements
        or observation.form_fields
        or observation.dialogs
    )


def _has_page_issue(observation: PageObservation, code: PageIssueCode) -> bool:
    return any(issue.code is code for issue in observation.issues)


def _can_auto_dismiss_modal(observation: PageObservation) -> bool:
    issue_codes = {issue.code for issue in observation.issues}
    if PageIssueCode.MODAL_DIALOG not in issue_codes:
        return False
    requires_user_handling = {
        PageIssueCode.BLOCKED_PAGE,
        PageIssueCode.CAPTCHA_BLOCKING_PAGE,
        PageIssueCode.LOGIN_WALL,
        PageIssueCode.REGION_PROMPT,
    }
    return issue_codes.isdisjoint(requires_user_handling)


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


def _run_evidence_details(
    observed_resource_urls: set[str],
    visited_target_urls: set[str],
    repeated_target_preventions: int,
) -> Mapping[str, object]:
    return {
        "observed_resource_count": _dominant_visited_resource_count(observed_resource_urls),
        "observed_resource_urls": sorted(observed_resource_urls),
        "visited_target_count": len({_target_identity(url) for url in visited_target_urls}),
        "repeated_target_preventions": repeated_target_preventions,
    }


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
    if result.termination_reason is TaskTerminationReason.PARTIAL_RESULT:
        return "Защитный лимит достигнут. Агент вернул все проверенные данные, собранные к этому моменту."
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
        if "browser observation" in (result.message or "").casefold():
            return (
                "Связь с браузером потеряна. Текущая задача остановлена без новых действий; "
                "перед продолжением требуется перезапуск браузерной сессии."
            )
        return (
            "На странице обнаружен блокер: CAPTCHA, login wall, региональный запрос, модальное окно или похожее препятствие. "
            "Агент не обходит такие проверки, не автоматизирует логин и записывает причину в отчет."
        )
    if result.termination_reason is TaskTerminationReason.MAX_ITERATIONS_EXCEEDED:
        return (
            "Достигнут лимит автономных шагов агента для одной задачи. "
            "Увеличьте --max-actions или начните с более конкретной страницы."
        )
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
    sensitive_fields = schema.input_schema.sensitive_field_names() if schema is not None else set()
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
