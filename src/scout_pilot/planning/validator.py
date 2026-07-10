"""Validation for provider-generated plans."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scout_pilot.models import ExecutionPlan, PlanStep
from scout_pilot.planning.types import (
    PlanValidationIssue,
    PlanValidationResult,
    PlanValidationSeverity,
    PlanningSettings,
)
from scout_pilot.tools.types import ToolSchema


_XPATH_PATTERN = re.compile(
    r"xpath\s*=|(?<!:)//|/html/|contains\s*\(@|text\s*\(\)",
    re.IGNORECASE,
)
_CSS_OR_PLAYWRIGHT_PATTERN = re.compile(
    r"querySelector|locator\s*\(|get_by_|css\s*=|\[data-|\[aria-|nth-child|"
    r"(^|\s)[.#][A-Za-z_][\w-]*",
    re.IGNORECASE,
)
_ROUTE_PATH_PATTERN = re.compile(
    r"(^|\s)/(?!/)[A-Za-z0-9][A-Za-z0-9._~/-]*",
    re.IGNORECASE,
)
_SIDE_EFFECT_PATTERN = re.compile(
    r"\b(submit|send|delete|remove|purchase|buy|checkout|publish|upload|apply|"
    r"unsubscribe|cancel|confirm|pay|order)\b",
    re.IGNORECASE,
)


def validate_plan(
    plan: ExecutionPlan,
    available_tools: Sequence[ToolSchema] = (),
    settings: PlanningSettings | None = None,
) -> PlanValidationResult:
    """Validate a plan without executing browser or tool logic."""

    resolved_settings = settings or PlanningSettings()
    issues: list[PlanValidationIssue] = []
    tool_by_name = {tool.name: tool for tool in available_tools}

    if not plan.steps:
        issues.append(
            PlanValidationIssue(
                code="empty_plan",
                message="Plan must contain at least one step.",
            )
        )

    if len(plan.steps) > resolved_settings.max_steps:
        issues.append(
            PlanValidationIssue(
                code="too_many_steps",
                message=f"Plan has more than {resolved_settings.max_steps} steps.",
            )
        )

    for step in plan.steps:
        issues.extend(_validate_step_text(step))
        if step.tool_name:
            if available_tools and step.tool_name not in tool_by_name:
                issues.append(
                    PlanValidationIssue(
                        code="unknown_tool",
                        message=f"Step references unknown tool '{step.tool_name}'.",
                        step_id=step.step_id,
                    )
                )
            elif step.tool_name in tool_by_name:
                validation = tool_by_name[step.tool_name].input_schema.validate(
                    step.arguments
                )
                for error in validation.errors:
                    issues.append(
                        PlanValidationIssue(
                            code="invalid_tool_arguments",
                            message=(
                                f"Step tool arguments are invalid for "
                                f"'{step.tool_name}': {error.field} - {error.message}"
                            ),
                            step_id=step.step_id,
                        )
                    )
        if _may_have_external_effect(step) and not step.requires_confirmation:
            issues.append(
                PlanValidationIssue(
                    code="confirmation_recommended",
                    message=(
                        "Step appears to have an external side effect and should "
                        "require user confirmation."
                    ),
                    severity=PlanValidationSeverity.WARNING,
                    step_id=step.step_id,
                )
            )

    return PlanValidationResult(issues=tuple(issues))


def invalid_step_ids(result: PlanValidationResult) -> set[str]:
    """Return step IDs with blocking validation errors."""

    return {
        issue.step_id
        for issue in result.errors
        if issue.step_id is not None
    }


def _validate_step_text(step: PlanStep) -> Iterable[PlanValidationIssue]:
    values = [
        step.goal,
        step.tool_name,
        step.rationale,
        step.uncertainty_reason,
        step.notes,
        *_string_values(step.arguments),
    ]
    joined = "\n".join(value for value in values if value)
    if not joined.strip():
        yield PlanValidationIssue(
            code="empty_step_goal",
            message="Step goal cannot be empty.",
            step_id=step.step_id,
        )
        return

    if _XPATH_PATTERN.search(joined):
        yield PlanValidationIssue(
            code="implementation_detail",
            message="Plan step must not include XPath or DOM traversal details.",
            step_id=step.step_id,
        )
    if _CSS_OR_PLAYWRIGHT_PATTERN.search(joined):
        yield PlanValidationIssue(
            code="implementation_detail",
            message="Plan step must not include CSS selectors or Playwright locators.",
            step_id=step.step_id,
        )
    if _ROUTE_PATH_PATTERN.search(joined):
        yield PlanValidationIssue(
            code="hardcoded_route",
            message="Plan step should not depend on hardcoded route paths.",
            severity=PlanValidationSeverity.WARNING,
            step_id=step.step_id,
        )


def _string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _string_values(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for item in value:
            yield from _string_values(item)


def _may_have_external_effect(step: PlanStep) -> bool:
    values = [
        step.goal,
        step.rationale,
        step.notes,
        step.tool_name,
        *_string_values(step.arguments),
    ]
    return bool(_SIDE_EFFECT_PATTERN.search("\n".join(value for value in values if value)))
