"""Provider-backed Planning Engine implementation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from scout_pilot.context import (
    ContextBudgetSettings,
    ContextCompressionMetrics,
    DeterministicContextBudgeter,
)
from scout_pilot.llm.provider import LlmProvider
from scout_pilot.llm.types import LlmProviderRequest
from scout_pilot.models import (
    ExecutionPlan,
    PageObservation,
    PlanStep,
    PlanStepStatus,
    ToolRequest,
    UserTask,
)
from scout_pilot.planning.templates import (
    build_create_plan_messages,
    build_revision_plan_messages,
)
from scout_pilot.planning.types import PlanningSettings
from scout_pilot.planning.validator import invalid_step_ids, validate_plan
from scout_pilot.tools.types import ToolSchema


_CONFIRMATION_PATTERN = re.compile(
    r"\b(submit|send|delete|remove|purchase|buy|checkout|publish|upload|apply|"
    r"unsubscribe|cancel|confirm|pay|order)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_PATTERN = re.compile(r"\b(do it|that|this|same thing|continue)\b", re.IGNORECASE)


class PlanParsingError(ValueError):
    """Raised when a provider response cannot be converted into a plan."""


class ProviderPlanningEngine:
    """Create and revise execution plans through a provider-neutral LLM."""

    def __init__(
        self,
        provider: LlmProvider,
        settings: PlanningSettings | None = None,
        context_budgeter: DeterministicContextBudgeter | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings or PlanningSettings()
        self._context_budgeter = context_budgeter or DeterministicContextBudgeter(
            ContextBudgetSettings(
                max_input_tokens=self._settings.max_input_tokens,
                reserved_output_tokens=self._settings.max_output_tokens,
                max_observation_tokens=self._settings.max_prompt_observation_tokens,
                max_memory_tokens=self._settings.max_memory_tokens,
                max_memory_summaries=self._settings.max_memory_summaries,
            )
        )
        self.last_context_metrics: ContextCompressionMetrics | None = None

    async def create_plan(
        self,
        task: UserTask,
        observation: PageObservation | None,
        memory_summaries: Sequence[str] = (),
        available_tools: Sequence[ToolSchema] = (),
    ) -> ExecutionPlan:
        """Create an initial plan for a validated user task."""

        return await self.create_plan_from_text(
            task.text,
            observation=observation,
            memory_summaries=memory_summaries,
            available_tools=available_tools,
        )

    async def create_plan_from_text(
        self,
        task_text: str,
        observation: PageObservation | None = None,
        memory_summaries: Sequence[str] = (),
        available_tools: Sequence[ToolSchema] = (),
    ) -> ExecutionPlan:
        """Create a plan, including deterministic fallbacks for invalid text."""

        preflight_message = _preflight_message(task_text)
        if preflight_message is not None:
            self.last_context_metrics = None
            return _fallback_plan(
                task_text=task_text,
                observation=observation,
                memory_summaries=memory_summaries,
                message=preflight_message,
                validation_errors=(preflight_message,),
                source="fallback",
            )

        budgeted = self._context_budgeter.assemble(
            user_task=task_text.strip(),
            observation=observation,
            memory_summaries=memory_summaries,
            max_input_tokens=self._settings.max_input_tokens,
            reserved_output_tokens=self._settings.max_output_tokens,
        )
        self.last_context_metrics = budgeted.metrics

        messages = build_create_plan_messages(
            user_task=task_text.strip(),
            observation=budgeted.observation,
            memory_summaries=budgeted.memory_summaries,
            available_tools=available_tools,
            settings=self._settings,
            context_metrics=budgeted.metrics,
        )
        result = await self._complete(messages)
        if not result.success:
            return _fallback_plan(
                task_text=task_text,
                observation=budgeted.observation,
                memory_summaries=budgeted.memory_summaries,
                message=_provider_failure_message(result.error),
                validation_errors=(_provider_failure_message(result.error),),
                source="fallback",
                available_tools=available_tools,
            )

        try:
            plan = _plan_from_provider_payload(
                task_text=task_text,
                observation=budgeted.observation,
                memory_summaries=budgeted.memory_summaries,
                payload=_load_plan_json(result.response.content if result.response else None),
                source="planner",
                settings=self._settings,
            )
        except PlanParsingError as exc:
            return _fallback_plan(
                task_text=task_text,
                observation=budgeted.observation,
                memory_summaries=budgeted.memory_summaries,
                message=f"Planner response could not be parsed: {exc}",
                validation_errors=(f"Planner response could not be parsed: {exc}",),
                source="fallback",
                available_tools=available_tools,
            )

        return _finalize_plan(
            plan,
            available_tools=available_tools,
            settings=self._settings,
        )

    async def revise_plan(
        self,
        plan: ExecutionPlan,
        observation: PageObservation | None,
        reason: str,
        memory_summaries: Sequence[str] = (),
        available_tools: Sequence[ToolSchema] = (),
    ) -> ExecutionPlan:
        """Revise a plan without losing already completed steps."""

        budgeted = self._context_budgeter.assemble(
            user_task=plan.task.text,
            observation=observation,
            memory_summaries=memory_summaries or plan.memory_summaries,
            max_input_tokens=self._settings.max_input_tokens,
            reserved_output_tokens=self._settings.max_output_tokens,
        )
        self.last_context_metrics = budgeted.metrics

        messages = build_revision_plan_messages(
            plan=plan,
            observation=budgeted.observation,
            reason=reason,
            memory_summaries=budgeted.memory_summaries,
            available_tools=available_tools,
            settings=self._settings,
            context_metrics=budgeted.metrics,
        )
        completed_steps = tuple(
            step for step in plan.steps if step.status is PlanStepStatus.COMPLETED
        )
        result = await self._complete(messages)
        if not result.success:
            fallback = _fallback_plan(
                task_text=plan.task.text,
                observation=budgeted.observation,
                memory_summaries=budgeted.memory_summaries,
                message=_provider_failure_message(result.error),
                validation_errors=(_provider_failure_message(result.error),),
                source="fallback",
                available_tools=available_tools,
                completed_steps=completed_steps,
                revision_reason=reason,
            )
            return fallback

        try:
            revised = _plan_from_provider_payload(
                task_text=plan.task.text,
                observation=budgeted.observation,
                memory_summaries=budgeted.memory_summaries,
                payload=_load_plan_json(result.response.content if result.response else None),
                source="planner_revision",
                settings=self._settings,
                revision_reason=reason,
            )
        except PlanParsingError as exc:
            return _fallback_plan(
                task_text=plan.task.text,
                observation=budgeted.observation,
                memory_summaries=budgeted.memory_summaries,
                message=f"Planner response could not be parsed: {exc}",
                validation_errors=(f"Planner response could not be parsed: {exc}",),
                source="fallback",
                available_tools=available_tools,
                completed_steps=completed_steps,
                revision_reason=reason,
            )

        merged_steps = _merge_completed_steps(completed_steps, revised.steps)
        merged = ExecutionPlan(
            task=revised.task,
            steps=merged_steps,
            summary=revised.summary,
            warnings=revised.warnings,
            validation_errors=revised.validation_errors,
            source=revised.source,
            observation_url=revised.observation_url,
            observation_summary=revised.observation_summary,
            memory_summaries=revised.memory_summaries,
            is_fallback=revised.is_fallback,
            revision_reason=reason,
        )
        return _finalize_plan(
            merged,
            available_tools=available_tools,
            settings=self._settings,
        )

    async def _complete(
        self,
        messages,
    ):
        request = LlmProviderRequest(
            messages=messages,
            tools=(),
            max_output_tokens=self._settings.max_output_tokens,
            timeout_seconds=self._settings.timeout_seconds,
        )
        try:
            return await self._provider.complete(request)
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            from scout_pilot.llm.types import (
                LlmErrorCode,
                LlmProviderError,
                LlmProviderResult,
            )

            return LlmProviderResult(
                success=False,
                error=LlmProviderError(
                    code=LlmErrorCode.PROVIDER_UNAVAILABLE,
                    message=str(exc),
                    retryable=True,
                ),
            )


def _preflight_message(task_text: str) -> str | None:
    stripped = task_text.strip()
    if not stripped:
        return "User task is empty; ask the user for a concrete browser goal."
    words = [word for word in re.split(r"\s+", stripped) if word]
    if len(words) < 2 and not re.match(r"https?://", stripped, flags=re.IGNORECASE):
        return "User task is too ambiguous to plan safely."
    if len(words) <= 4 and _AMBIGUOUS_PATTERN.search(stripped):
        return "User task refers to missing context and needs clarification."
    return None


def _fallback_plan(
    task_text: str,
    observation: PageObservation | None,
    memory_summaries: Sequence[str],
    message: str,
    validation_errors: Sequence[str] = (),
    source: str = "fallback",
    available_tools: Sequence[ToolSchema] = (),
    completed_steps: Sequence[PlanStep] = (),
    revision_reason: str | None = None,
) -> ExecutionPlan:
    task = _safe_task(task_text)
    requires_confirmation = _CONFIRMATION_PATTERN.search(task_text) is not None
    observe_tool_name = _observe_tool_name(available_tools)
    action_step = PlanStep(
        step_id="fallback_confirm" if requires_confirmation else "fallback_clarify",
        goal=(
            "Ask the user to confirm the requested side-effect before planning browser actions."
            if requires_confirmation
            else "Ask the user for the missing browser goal details before taking action."
        ),
        tool_name=None,
        requires_confirmation=requires_confirmation,
        is_uncertain=True,
        uncertainty_reason=message,
        notes=message,
    )
    if not validation_errors and observe_tool_name:
        action_step = PlanStep(
            step_id="fallback_observe",
            goal="Capture a fresh semantic observation before deciding the next browser action.",
            tool_name=observe_tool_name,
            arguments={},
            tool_request=ToolRequest(name=observe_tool_name, arguments={}),
            is_uncertain=True,
            uncertainty_reason=message,
            notes=message,
        )

    return ExecutionPlan(
        task=task,
        steps=tuple(completed_steps) + (action_step,),
        summary=message,
        warnings=(message,),
        validation_errors=tuple(validation_errors),
        source=source,
        observation_url=observation.url if observation else None,
        observation_summary=observation.summary if observation else None,
        memory_summaries=memory_summaries,
        is_fallback=True,
        revision_reason=revision_reason,
    )


def _plan_from_provider_payload(
    task_text: str,
    observation: PageObservation | None,
    memory_summaries: Sequence[str],
    payload: Mapping[str, Any],
    source: str,
    settings: PlanningSettings,
    revision_reason: str | None = None,
) -> ExecutionPlan:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise PlanParsingError("expected 'steps' to be a list")

    warnings = _string_list(payload.get("warnings"))
    if len(raw_steps) > settings.max_steps:
        warnings.append(f"Planner returned more than {settings.max_steps} steps; extras were ignored.")

    steps = tuple(
        _step_from_payload(raw_step, index)
        for index, raw_step in enumerate(raw_steps[: settings.max_steps], start=1)
    )
    return ExecutionPlan(
        task=_safe_task(task_text),
        steps=_apply_confirmation_heuristics(task_text, steps),
        summary=_string_or_default(payload.get("summary"), "Plan created."),
        warnings=warnings,
        source=source,
        observation_url=observation.url if observation else None,
        observation_summary=observation.summary if observation else None,
        memory_summaries=memory_summaries,
        revision_reason=revision_reason,
    )


def _step_from_payload(raw_step: Any, index: int) -> PlanStep:
    if not isinstance(raw_step, Mapping):
        raise PlanParsingError(f"step {index} is not an object")
    goal = _string_or_default(raw_step.get("goal"), f"Complete plan step {index}.")
    tool_name = _optional_string(raw_step.get("tool_name") or raw_step.get("tool"))
    arguments = raw_step.get("arguments")
    if not isinstance(arguments, Mapping):
        arguments = {}
    status = _status_from_value(raw_step.get("status"))
    tool_request = (
        ToolRequest(name=tool_name, arguments=dict(arguments))
        if tool_name is not None
        else None
    )
    return PlanStep(
        step_id=_string_or_default(
            raw_step.get("step_id") or raw_step.get("id"),
            f"step_{index}",
        ),
        goal=goal,
        status=status,
        tool_request=tool_request,
        tool_name=tool_name,
        arguments=dict(arguments),
        rationale=_optional_string(raw_step.get("rationale")),
        requires_confirmation=bool(raw_step.get("requires_confirmation", False)),
        is_uncertain=bool(raw_step.get("is_uncertain", False)),
        uncertainty_reason=_optional_string(raw_step.get("uncertainty_reason")),
        notes=_optional_string(raw_step.get("notes")),
    )


def _load_plan_json(content: str | None) -> Mapping[str, Any]:
    if not content or not content.strip():
        raise PlanParsingError("empty provider response")
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise PlanParsingError("response does not contain a JSON object")
    try:
        loaded = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanParsingError(str(exc)) from exc
    if not isinstance(loaded, Mapping):
        raise PlanParsingError("top-level JSON value must be an object")
    return loaded


def _finalize_plan(
    plan: ExecutionPlan,
    available_tools: Sequence[ToolSchema],
    settings: PlanningSettings,
) -> ExecutionPlan:
    validation = validate_plan(plan, available_tools=available_tools, settings=settings)
    invalid_ids = invalid_step_ids(validation)
    steps = tuple(
        _deactivate_invalid_step(step, validation.error_messages())
        if step.step_id in invalid_ids
        else step
        for step in plan.steps
    )
    return ExecutionPlan(
        task=plan.task,
        steps=steps,
        summary=plan.summary,
        warnings=(*plan.warnings, *validation.warning_messages()),
        validation_errors=(*plan.validation_errors, *validation.error_messages()),
        source=plan.source,
        observation_url=plan.observation_url,
        observation_summary=plan.observation_summary,
        memory_summaries=plan.memory_summaries,
        is_fallback=plan.is_fallback,
        revision_reason=plan.revision_reason,
    )


def _deactivate_invalid_step(step: PlanStep, messages: Sequence[str]) -> PlanStep:
    reason = "; ".join(messages) or "Plan step failed validation."
    return replace(
        step,
        tool_request=None,
        tool_name=None,
        arguments={},
        is_uncertain=True,
        uncertainty_reason=step.uncertainty_reason or reason,
    )


def _apply_confirmation_heuristics(
    task_text: str,
    steps: Sequence[PlanStep],
) -> tuple[PlanStep, ...]:
    if not steps:
        return tuple()
    task_requires_confirmation = _CONFIRMATION_PATTERN.search(task_text) is not None
    adjusted: list[PlanStep] = []
    for index, step in enumerate(steps):
        step_text = " ".join(
            value
            for value in (
                step.goal,
                step.rationale or "",
                step.notes or "",
                step.tool_name or "",
            )
            if value
        )
        step_requires_confirmation = _CONFIRMATION_PATTERN.search(step_text) is not None
        is_last_step = index == len(steps) - 1
        if step_requires_confirmation or (task_requires_confirmation and is_last_step):
            adjusted.append(replace(step, requires_confirmation=True))
        else:
            adjusted.append(step)
    return tuple(adjusted)


def _merge_completed_steps(
    completed_steps: Sequence[PlanStep],
    revised_steps: Sequence[PlanStep],
) -> tuple[PlanStep, ...]:
    completed_ids = {step.step_id for step in completed_steps}
    new_steps = tuple(step for step in revised_steps if step.step_id not in completed_ids)
    return tuple(completed_steps) + new_steps


def _provider_failure_message(error) -> str:
    if error is None:
        return "Planner provider failed without a structured error."
    return f"Planner provider failed: {error.message}"


def _safe_task(task_text: str) -> UserTask:
    stripped = task_text.strip()
    return UserTask(stripped or "Clarify the browser task")


def _observe_tool_name(available_tools: Sequence[ToolSchema]) -> str | None:
    if any(tool.name == "browser.observe" for tool in available_tools):
        return "browser.observe"
    return None


def _string_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _status_from_value(value: Any) -> PlanStepStatus:
    if isinstance(value, str):
        try:
            return PlanStepStatus(value)
        except ValueError:
            return PlanStepStatus.PENDING
    return PlanStepStatus.PENDING
