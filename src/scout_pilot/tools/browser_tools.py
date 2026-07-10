"""Browser tool implementations built on high-level application interfaces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from scout_pilot.browser.types import BrowserActionResult, ScreenshotResult
from scout_pilot.navigation import (
    NavigationIntent,
    NavigationIntentKind,
    SemanticNavigationResolver,
    SemanticResolution,
    SemanticResolutionStatus,
)
from scout_pilot.tools.base import ToolContext
from scout_pilot.tools.registry import ToolRegistry
from scout_pilot.tools.types import (
    ToolExecutionOutcome,
    ToolFailureKind,
    ToolFieldSchema,
    ToolInputSchema,
    ToolOutputSchema,
    ToolValueType,
)


RETRYABLE_BROWSER_CODES = {
    "browser_not_started",
    "semantic_element_not_found",
    "navigation_timeout",
    "reload_timeout",
    "back_timeout",
    "forward_timeout",
    "click_timeout",
    "fill_timeout",
    "wait_error",
    "press_key_error",
    "semantic_target_not_found",
    "semantic_element_stale",
    "browser_closed",
}
NON_RETRYABLE_BROWSER_CODES = {
    "invalid_url",
    "invalid_semantic_id",
    "invalid_key",
    "invalid_wait_duration",
    "invalid_field_value",
    "element_not_fillable",
    "semantic_target_ambiguous",
    "semantic_resolution_invalid",
}


def _browser_action_output_schema() -> ToolOutputSchema:
    return ToolOutputSchema(
        fields=(
            ToolFieldSchema("action", ToolValueType.STRING, "Browser action name."),
            ToolFieldSchema("url", ToolValueType.STRING, "Current page URL.", required=False),
            ToolFieldSchema("title", ToolValueType.STRING, "Current page title.", required=False),
        )
    )


@dataclass(frozen=True)
class NavigateTool:
    name: str = "browser.navigate"
    description: str = "Navigate the browser to a user-provided or discovered URL."
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="url",
                value_type=ToolValueType.STRING,
                description="Absolute URL to open.",
                min_length=1,
                max_length=4096,
            ),
        )
    )
    output_schema: ToolOutputSchema = _browser_action_output_schema()
    timeout_seconds: float = 20.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        result = await browser.navigate_to(str(arguments["url"]))
        return _outcome_from_browser_action(result)


@dataclass(frozen=True)
class ClickTool:
    name: str = "browser.click"
    description: str = "Click a visible interactive element by semantic element ID."
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="element_id",
                value_type=ToolValueType.STRING,
                description="Generated semantic element ID from the latest observation.",
                min_length=1,
                max_length=128,
            ),
        )
    )
    output_schema: ToolOutputSchema = _browser_action_output_schema()
    timeout_seconds: float = 10.0
    stale_recovery_attempts: int = 1

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        element_id = str(arguments["element_id"])
        if context.observation_engine is None:
            result = await browser.click_by_semantic_id(element_id)
            return _outcome_from_browser_action(result)

        observation_engine = context.observation_engine
        resolver = SemanticNavigationResolver()
        before = await observation_engine.observe()
        result = await browser.click_by_semantic_id(element_id)
        recovered = False
        resolution: SemanticResolution | None = None
        source_observation = before
        source_id = element_id
        for _ in range(max(0, self.stale_recovery_attempts)):
            if not _is_stale_element_error(result.error_code):
                break
            refreshed = await observation_engine.observe()
            remapped = resolver.remap_click_candidate(source_observation, refreshed, source_id)
            if not remapped.is_resolved:
                return _resolution_failure(remapped)
            resolution = remapped
            recovered = True
            source_observation = refreshed
            source_id = remapped.selected.element_id
            result = await browser.click_by_semantic_id(source_id)

        if recovered and resolution is not None:
            after = await observation_engine.observe()
            return _outcome_from_semantic_action(
                result,
                resolution=resolution,
                transition=resolver.detect_transition(before, after),
                recovered_from_stale=True,
            )
        return _outcome_from_browser_action(result)


@dataclass(frozen=True)
class FillTool:
    name: str = "browser.fill"
    description: str = "Fill a form field by semantic element or field ID."
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="element_id",
                value_type=ToolValueType.STRING,
                description="Generated semantic element or field ID from the latest observation.",
                min_length=1,
                max_length=128,
            ),
            ToolFieldSchema(
                name="value",
                value_type=ToolValueType.STRING,
                description="Value to enter into the field.",
                sensitive=True,
                max_length=10000,
            ),
        )
    )
    output_schema: ToolOutputSchema = _browser_action_output_schema()
    timeout_seconds: float = 10.0
    stale_recovery_attempts: int = 1

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        element_id = str(arguments["element_id"])
        value = str(arguments["value"])
        if context.observation_engine is None:
            result = await browser.fill_by_semantic_id(element_id, value)
            return _outcome_from_browser_action(result)

        observation_engine = context.observation_engine
        resolver = SemanticNavigationResolver()
        before = await observation_engine.observe()
        result = await browser.fill_by_semantic_id(element_id, value)
        recovered = False
        resolution: SemanticResolution | None = None
        source_observation = before
        source_id = element_id
        for _ in range(max(0, self.stale_recovery_attempts)):
            if not _is_stale_element_error(result.error_code):
                break
            refreshed = await observation_engine.observe()
            remapped = resolver.remap_field_candidate(source_observation, refreshed, source_id)
            if not remapped.is_resolved:
                return _resolution_failure(remapped)
            resolution = remapped
            recovered = True
            source_observation = refreshed
            source_id = remapped.selected.element_id
            result = await browser.fill_by_semantic_id(source_id, value)

        if recovered and resolution is not None:
            after = await observation_engine.observe()
            return _outcome_from_semantic_action(
                result,
                resolution=resolution,
                transition=resolver.detect_transition(before, after),
                recovered_from_stale=True,
            )
        return _outcome_from_browser_action(result)


@dataclass(frozen=True)
class ResolveSemanticTargetTool:
    name: str = "browser.resolve_target"
    description: str = (
        "Resolve a website-neutral semantic intent to visible observation IDs without clicking."
    )
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="kind",
                value_type=ToolValueType.STRING,
                description="Intent kind to resolve.",
                enum_values=("click", "field", "search_field"),
            ),
            ToolFieldSchema(
                name="target",
                value_type=ToolValueType.STRING,
                description="Human-visible target text, label or accessible name.",
                required=False,
                max_length=240,
            ),
            ToolFieldSchema(
                name="role",
                value_type=ToolValueType.STRING,
                description="Optional semantic role hint such as link, button or textbox.",
                required=False,
                max_length=64,
            ),
            ToolFieldSchema(
                name="context",
                value_type=ToolValueType.STRING,
                description="Optional visible context to disambiguate similar targets.",
                required=False,
                max_length=240,
            ),
        )
    )
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(
            ToolFieldSchema(
                "resolution",
                ToolValueType.OBJECT,
                "Semantic resolution result with visible candidate IDs.",
            ),
        )
    )
    timeout_seconds: float = 10.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        observation_engine = _require_observation_engine(context)
        observation = await observation_engine.observe()
        intent = _intent_from_arguments(arguments)
        resolution = SemanticNavigationResolver().resolve(observation, intent)
        return ToolExecutionOutcome(
            success=resolution.status is not SemanticResolutionStatus.INVALID,
            message=resolution.message,
            data={"resolution": resolution.to_dict()},
            failure_kind=(
                ToolFailureKind.VALIDATION
                if resolution.status is SemanticResolutionStatus.INVALID
                else None
            ),
            retryable=False,
            error_code=(
                "semantic_resolution_invalid"
                if resolution.status is SemanticResolutionStatus.INVALID
                else None
            ),
        )


@dataclass(frozen=True)
class ClickByIntentTool:
    name: str = "browser.click_by_intent"
    description: str = (
        "Click a visible interactive element by semantic intent using role, name and context."
    )
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="target",
                value_type=ToolValueType.STRING,
                description="Visible text, accessible name or intent phrase to click.",
                min_length=1,
                max_length=240,
            ),
            ToolFieldSchema(
                name="role",
                value_type=ToolValueType.STRING,
                description="Optional role hint such as link, button, tab or menuitem.",
                required=False,
                max_length=64,
            ),
            ToolFieldSchema(
                name="context",
                value_type=ToolValueType.STRING,
                description="Optional visible context for disambiguation.",
                required=False,
                max_length=240,
            ),
        )
    )
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(
            *_browser_action_output_schema().fields,
            ToolFieldSchema(
                "resolution",
                ToolValueType.OBJECT,
                "Semantic candidate selected for the click.",
            ),
            ToolFieldSchema(
                "transition",
                ToolValueType.OBJECT,
                "Semantic page transition detected after the click.",
            ),
        )
    )
    timeout_seconds: float = 20.0
    stale_recovery_attempts: int = 1

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        observation_engine = _require_observation_engine(context)
        resolver = SemanticNavigationResolver()
        before = await observation_engine.observe()
        resolution = resolver.resolve_click(
            before,
            target=str(arguments["target"]),
            role=_optional_argument(arguments, "role"),
            context=_optional_argument(arguments, "context"),
        )
        if not resolution.is_resolved:
            return _resolution_failure(resolution)

        result = await browser.click_by_semantic_id(resolution.selected.element_id)
        recovered = False
        source_observation = before
        source_id = resolution.selected.element_id
        for _ in range(max(0, self.stale_recovery_attempts)):
            if not _is_stale_element_error(result.error_code):
                break
            refreshed = await observation_engine.observe()
            remapped = resolver.remap_click_candidate(source_observation, refreshed, source_id)
            if not remapped.is_resolved:
                return _resolution_failure(remapped)
            resolution = remapped
            recovered = True
            source_observation = refreshed
            source_id = resolution.selected.element_id
            result = await browser.click_by_semantic_id(source_id)

        after = await observation_engine.observe()
        return _outcome_from_semantic_action(
            result,
            resolution=resolution,
            transition=resolver.detect_transition(before, after),
            recovered_from_stale=recovered,
        )


@dataclass(frozen=True)
class FillByLabelTool:
    name: str = "browser.fill_by_label"
    description: str = (
        "Fill a visible form field by semantic label, accessible name or generic search-field intent."
    )
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="label",
                value_type=ToolValueType.STRING,
                description="Field label, accessible name, placeholder or generic search intent.",
                min_length=1,
                max_length=240,
            ),
            ToolFieldSchema(
                name="value",
                value_type=ToolValueType.STRING,
                description="Value to enter into the field.",
                sensitive=True,
                max_length=10000,
            ),
            ToolFieldSchema(
                name="context",
                value_type=ToolValueType.STRING,
                description="Optional visible context for disambiguation.",
                required=False,
                max_length=240,
            ),
        )
    )
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(
            *_browser_action_output_schema().fields,
            ToolFieldSchema(
                "resolution",
                ToolValueType.OBJECT,
                "Semantic field selected for filling.",
            ),
            ToolFieldSchema(
                "transition",
                ToolValueType.OBJECT,
                "Semantic page transition detected after filling.",
            ),
        )
    )
    timeout_seconds: float = 20.0
    stale_recovery_attempts: int = 1

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        observation_engine = _require_observation_engine(context)
        resolver = SemanticNavigationResolver()
        before = await observation_engine.observe()
        resolution = resolver.resolve_form_field(
            before,
            str(arguments["label"]),
            _optional_argument(arguments, "context"),
        )
        if not resolution.is_resolved:
            return _resolution_failure(resolution)

        result = await browser.fill_by_semantic_id(
            resolution.selected.element_id,
            str(arguments["value"]),
        )
        recovered = False
        source_observation = before
        source_id = resolution.selected.element_id
        for _ in range(max(0, self.stale_recovery_attempts)):
            if not _is_stale_element_error(result.error_code):
                break
            refreshed = await observation_engine.observe()
            remapped = resolver.remap_field_candidate(source_observation, refreshed, source_id)
            if not remapped.is_resolved:
                return _resolution_failure(remapped)
            resolution = remapped
            recovered = True
            source_observation = refreshed
            source_id = resolution.selected.element_id
            result = await browser.fill_by_semantic_id(source_id, str(arguments["value"]))

        after = await observation_engine.observe()
        return _outcome_from_semantic_action(
            result,
            resolution=resolution,
            transition=resolver.detect_transition(before, after),
            recovered_from_stale=recovered,
        )


@dataclass(frozen=True)
class PlanFormFillTool:
    name: str = "browser.plan_form_fill"
    description: str = (
        "Plan generic form filling by matching requested labels to semantic field IDs."
    )
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="field_labels",
                value_type=ToolValueType.ARRAY,
                description="Field labels or accessible names to map to current form fields.",
            ),
        )
    )
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(
            ToolFieldSchema(
                "form_fill_plan",
                ToolValueType.OBJECT,
                "Field label to semantic ID mapping without field values.",
            ),
        )
    )
    timeout_seconds: float = 10.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        observation_engine = _require_observation_engine(context)
        labels = [str(item) for item in arguments["field_labels"]]
        observation = await observation_engine.observe()
        plan = SemanticNavigationResolver().plan_form_fill(observation, labels)
        return ToolExecutionOutcome(
            success=plan.is_complete,
            message=(
                "Form fill plan created."
                if plan.is_complete
                else "Some form fields could not be resolved safely."
            ),
            data={"form_fill_plan": plan.to_dict()},
            failure_kind=None if plan.is_complete else ToolFailureKind.BROWSER,
            retryable=not plan.is_complete,
            error_code=None if plan.is_complete else "semantic_target_not_found",
        )


@dataclass(frozen=True)
class PressKeyTool:
    name: str = "browser.press_key"
    description: str = "Press a keyboard key on the current page."
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="key",
                value_type=ToolValueType.STRING,
                description="Playwright-compatible key name, such as Enter or Escape.",
                min_length=1,
                max_length=64,
            ),
        )
    )
    output_schema: ToolOutputSchema = _browser_action_output_schema()
    timeout_seconds: float = 5.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        result = await browser.press_key(str(arguments["key"]))
        return _outcome_from_browser_action(result)


@dataclass(frozen=True)
class WaitTool:
    name: str = "browser.wait"
    description: str = "Wait briefly for page state to settle."
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="milliseconds",
                value_type=ToolValueType.INTEGER,
                description="Wait duration in milliseconds.",
                minimum=0,
                maximum=60000,
            ),
        )
    )
    output_schema: ToolOutputSchema = _browser_action_output_schema()
    timeout_seconds: float = 65.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        result = await browser.wait_for_timeout(int(arguments["milliseconds"]))
        return _outcome_from_browser_action(result)


@dataclass(frozen=True)
class ScreenshotTool:
    name: str = "browser.screenshot"
    description: str = "Capture a diagnostic screenshot."
    input_schema: ToolInputSchema = ToolInputSchema(
        fields=(
            ToolFieldSchema(
                name="path",
                value_type=ToolValueType.STRING,
                description="Optional local screenshot path.",
                required=False,
                min_length=1,
                max_length=4096,
            ),
        )
    )
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(
            ToolFieldSchema("path", ToolValueType.STRING, "Local screenshot path."),
            ToolFieldSchema("url", ToolValueType.STRING, "Current page URL.", required=False),
            ToolFieldSchema("title", ToolValueType.STRING, "Current page title.", required=False),
        )
    )
    timeout_seconds: float = 10.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        raw_path = arguments.get("path")
        result = await browser.screenshot(Path(str(raw_path)) if raw_path else None)
        return _outcome_from_screenshot(result)


@dataclass(frozen=True)
class ObserveTool:
    name: str = "browser.observe"
    description: str = "Capture a compact semantic observation of the current page."
    input_schema: ToolInputSchema = ToolInputSchema()
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(
            ToolFieldSchema(
                "observation",
                ToolValueType.OBJECT,
                "LLM-safe semantic page observation.",
            ),
        )
    )
    timeout_seconds: float = 10.0

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        if context.observation_engine is None:
            return ToolExecutionOutcome(
                success=False,
                message="Observation engine is not configured.",
                failure_kind=ToolFailureKind.INTERNAL,
                retryable=False,
                error_code="observation_engine_missing",
            )
        observation = await context.observation_engine.observe()
        return ToolExecutionOutcome(
            success=True,
            message="Observation captured.",
            data={"observation": observation.to_llm_context()},
        )


def create_browser_tool_registry() -> ToolRegistry:
    """Create a registry with the standard browser tool set."""

    registry = ToolRegistry()
    for tool in (
        NavigateTool(),
        ClickTool(),
        FillTool(),
        ResolveSemanticTargetTool(),
        ClickByIntentTool(),
        FillByLabelTool(),
        PlanFormFillTool(),
        PressKeyTool(),
        WaitTool(),
        ScreenshotTool(),
        ObserveTool(),
    ):
        registry.register(tool)
    return registry


def _require_browser(context: ToolContext):
    if context.browser is None:
        raise RuntimeError("Browser engine is not configured.")
    return context.browser


def _require_observation_engine(context: ToolContext):
    if context.observation_engine is None:
        raise RuntimeError("Observation engine is not configured.")
    return context.observation_engine


def _outcome_from_browser_action(result: BrowserActionResult) -> ToolExecutionOutcome:
    data: dict[str, object] = {
        "action": result.action,
        "url": result.url,
        "title": result.title,
    }
    if result.success:
        return ToolExecutionOutcome(success=True, message=result.message, data=data)
    return ToolExecutionOutcome(
        success=False,
        message=result.message,
        data=data,
        failure_kind=ToolFailureKind.BROWSER,
        retryable=_is_retryable_browser_error(result.error_code),
        error_code=result.error_code,
    )


def _outcome_from_semantic_action(
    result: BrowserActionResult,
    *,
    resolution: SemanticResolution,
    transition,
    recovered_from_stale: bool,
) -> ToolExecutionOutcome:
    outcome = _outcome_from_browser_action(result)
    data = {
        **dict(outcome.data),
        "resolution": resolution.to_dict(),
        "transition": transition.to_dict(),
        "recovered_from_stale": recovered_from_stale,
    }
    return ToolExecutionOutcome(
        success=outcome.success,
        message=outcome.message,
        data=data,
        failure_kind=outcome.failure_kind,
        retryable=outcome.retryable,
        error_code=outcome.error_code,
    )


def _resolution_failure(resolution: SemanticResolution) -> ToolExecutionOutcome:
    error_code = {
        SemanticResolutionStatus.AMBIGUOUS: "semantic_target_ambiguous",
        SemanticResolutionStatus.INVALID: "semantic_resolution_invalid",
    }.get(resolution.status, "semantic_target_not_found")
    return ToolExecutionOutcome(
        success=False,
        message=resolution.message,
        data={"resolution": resolution.to_dict()},
        failure_kind=ToolFailureKind.BROWSER,
        retryable=resolution.status is SemanticResolutionStatus.NOT_FOUND,
        error_code=error_code,
    )


def _is_stale_element_error(error_code: str | None) -> bool:
    return error_code in {"semantic_element_not_found", "semantic_element_stale"}


def _outcome_from_screenshot(result: ScreenshotResult) -> ToolExecutionOutcome:
    data: dict[str, object] = {
        "path": str(result.path) if result.path is not None else None,
        "url": result.url,
        "title": result.title,
    }
    if result.success:
        return ToolExecutionOutcome(success=True, message=result.message, data=data)
    return ToolExecutionOutcome(
        success=False,
        message=result.message,
        data=data,
        failure_kind=ToolFailureKind.BROWSER,
        retryable=_is_retryable_browser_error(result.error_code),
        error_code=result.error_code,
    )


def _is_retryable_browser_error(error_code: str | None) -> bool:
    if error_code in NON_RETRYABLE_BROWSER_CODES:
        return False
    if error_code in RETRYABLE_BROWSER_CODES:
        return True
    if error_code and error_code.startswith("http_status_"):
        status_text = error_code.removeprefix("http_status_")
        return status_text.startswith("5")
    return bool(error_code and (error_code.endswith("_timeout") or error_code.endswith("_error")))


def _intent_from_arguments(arguments: Mapping[str, object]) -> NavigationIntent:
    kind = NavigationIntentKind(str(arguments["kind"]))
    return NavigationIntent(
        kind=kind,
        target=_optional_argument(arguments, "target"),
        role=_optional_argument(arguments, "role"),
        context=_optional_argument(arguments, "context"),
    )


def _optional_argument(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
