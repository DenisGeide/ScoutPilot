"""Prompt boundaries for provider-neutral planning."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from scout_pilot.context import ContextCompressionMetrics
from scout_pilot.llm.types import LlmMessage, LlmMessageRole
from scout_pilot.models import ExecutionPlan, PageObservation, PlanStep
from scout_pilot.planning.types import PlanningSettings
from scout_pilot.tools.types import ToolFieldSchema, ToolSchema


SYSTEM_PROMPT = """You are the Planning Engine for a browser automation agent.

Create short, practical execution plans. Decide what should happen, not how
Playwright should do it.

Rules:
- Stay website-neutral.
- Use semantic tool capabilities such as browser.observe, browser.navigate,
  browser.click, browser.fill, browser.press_key, browser.wait and
  browser.screenshot.
- Do not use CSS selectors, XPath, Playwright locators, DOM APIs, route paths
  or website-specific selectors.
- Mark uncertain steps honestly.
- Mark steps that may submit, purchase, delete, publish, send, upload or make
  external side effects as requiring confirmation.
- Return strict JSON only, with no markdown.

JSON shape:
{
  "summary": "one sentence",
  "steps": [
    {
      "goal": "short action goal",
      "tool_name": "optional available semantic tool name",
      "arguments": {},
      "rationale": "optional short reason",
      "requires_confirmation": false,
      "is_uncertain": false,
      "uncertainty_reason": null,
      "notes": null
    }
  ],
  "warnings": []
}
"""


def build_create_plan_messages(
    user_task: str,
    observation: PageObservation | None,
    memory_summaries: Sequence[str],
    available_tools: Sequence[ToolSchema],
    settings: PlanningSettings,
    context_metrics: ContextCompressionMetrics | None = None,
) -> tuple[LlmMessage, ...]:
    """Build provider-neutral messages for initial planning."""

    payload = {
        "mode": "create_plan",
        "user_goal": user_task,
        "current_observation": _observation_context(observation, settings),
        "memory_summaries": _bounded_strings(
            memory_summaries,
            settings.max_memory_summaries,
        ),
        "available_tools": _tool_summaries(available_tools, settings),
        "context_metrics": dict(context_metrics.to_dict()) if context_metrics else None,
    }
    return (
        LlmMessage(role=LlmMessageRole.SYSTEM, content=SYSTEM_PROMPT),
        LlmMessage(role=LlmMessageRole.USER, content=_json(payload)),
    )


def build_revision_plan_messages(
    plan: ExecutionPlan,
    observation: PageObservation | None,
    reason: str,
    memory_summaries: Sequence[str],
    available_tools: Sequence[ToolSchema],
    settings: PlanningSettings,
    context_metrics: ContextCompressionMetrics | None = None,
) -> tuple[LlmMessage, ...]:
    """Build provider-neutral messages for replanning."""

    payload = {
        "mode": "revise_plan",
        "revision_reason": reason,
        "user_goal": plan.task.text,
        "current_observation": _observation_context(observation, settings),
        "memory_summaries": _bounded_strings(
            memory_summaries or plan.memory_summaries,
            settings.max_memory_summaries,
        ),
        "available_tools": _tool_summaries(available_tools, settings),
        "context_metrics": dict(context_metrics.to_dict()) if context_metrics else None,
        "existing_plan": {
            "summary": plan.summary,
            "completed_steps_to_preserve": [
                _step_summary(step)
                for step in plan.steps
                if step.status.value == "completed"
            ],
            "remaining_steps": [
                _step_summary(step)
                for step in plan.steps
                if step.status.value != "completed"
            ],
        },
    }
    return (
        LlmMessage(role=LlmMessageRole.SYSTEM, content=SYSTEM_PROMPT),
        LlmMessage(role=LlmMessageRole.USER, content=_json(payload)),
    )


def _observation_context(
    observation: PageObservation | None,
    settings: PlanningSettings,
) -> Mapping[str, Any] | None:
    if observation is None:
        return None
    raw_context = observation.to_llm_context()
    serialized = _json(raw_context)
    if len(serialized) <= settings.max_prompt_observation_chars:
        return raw_context
    return {
        "url": observation.url,
        "title": observation.title,
        "summary": observation.summary[: settings.max_prompt_observation_chars],
        "truncated": True,
    }


def _tool_summaries(
    tools: Sequence[ToolSchema],
    settings: PlanningSettings,
) -> list[Mapping[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_fields": [_field_summary(field) for field in tool.input_schema.fields],
        }
        for tool in tools[: settings.max_tool_schemas]
    ]


def _field_summary(field: ToolFieldSchema) -> Mapping[str, Any]:
    return {
        "name": field.name,
        "type": field.value_type.value,
        "required": field.required,
        "sensitive": field.sensitive,
        "description": field.description,
    }


def _step_summary(step: PlanStep) -> Mapping[str, Any]:
    return {
        "step_id": step.step_id,
        "goal": step.goal,
        "status": step.status.value,
        "tool_name": step.tool_name,
        "requires_confirmation": step.requires_confirmation,
        "is_uncertain": step.is_uncertain,
    }


def _bounded_strings(values: Sequence[str], limit: int) -> list[str]:
    return [str(value) for value in values[:limit] if str(value).strip()]


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
