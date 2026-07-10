"""Browser tool implementations built on high-level application interfaces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from scout_pilot.browser.types import BrowserActionResult, ScreenshotResult
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
}
NON_RETRYABLE_BROWSER_CODES = {
    "invalid_url",
    "invalid_semantic_id",
    "invalid_key",
    "invalid_wait_duration",
    "invalid_field_value",
    "element_not_fillable",
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

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        result = await browser.click_by_semantic_id(str(arguments["element_id"]))
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

    async def execute(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolExecutionOutcome:
        browser = _require_browser(context)
        result = await browser.fill_by_semantic_id(
            str(arguments["element_id"]),
            str(arguments["value"]),
        )
        return _outcome_from_browser_action(result)


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
    return bool(error_code and (error_code.endswith("_timeout") or error_code.endswith("_error")))
