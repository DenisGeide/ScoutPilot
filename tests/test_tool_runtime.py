import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from scout_pilot.browser.types import BrowserActionResult, BrowserState, ScreenshotResult
from scout_pilot.models import ActionRisk, InteractiveElement, PageObservation, ToolRequest
from scout_pilot.tools import (
    DefaultToolRuntime,
    PreExecutionDecision,
    ToolContext,
    ToolExecutionOutcome,
    ToolExecutionStatus,
    ToolFailureKind,
    ToolFieldSchema,
    ToolInputSchema,
    ToolOutputSchema,
    ToolRegistry,
    ToolValueType,
    create_browser_tool_registry,
)


def test_tool_runtime_executes_success_and_records_structured_history(caplog):
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser, observation_engine=FakeObservationEngine()),
    )

    async def scenario():
        with caplog.at_level(logging.INFO, logger="scout_pilot.tools.runtime"):
            return await runtime.execute(
                ToolRequest(
                    name="browser.navigate",
                    arguments={"url": "https://example.test"},
                )
            )

    result = asyncio.run(scenario())

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.success is True
    assert result.data["url"] == "https://example.test"
    assert browser.actions == [("navigate", "https://example.test")]
    assert runtime.history[-1].tool_name == "browser.navigate"
    assert runtime.history[-1].arguments == {"url": "https://example.test"}
    assert runtime.security_audit_trail[-1].outcome == "allowed"
    assert "tool_execution_started" in caplog.text
    assert "tool_execution_finished" in caplog.text


def test_invalid_inputs_fail_before_browser_action():
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser, observation_engine=FakeObservationEngine()),
    )

    result = asyncio.run(runtime.execute(ToolRequest(name="browser.navigate", arguments={})))

    assert result.status is ToolExecutionStatus.VALIDATION_ERROR
    assert result.failure_kind is ToolFailureKind.VALIDATION
    assert result.validation_errors[0].field == "url"
    assert browser.actions == []


def test_timeout_is_structured_and_retryable():
    registry = ToolRegistry()
    registry.register(SlowTool())
    runtime = DefaultToolRuntime(registry, ToolContext())

    result = asyncio.run(runtime.execute(ToolRequest(name="slow.tool", arguments={})))

    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.failure_kind is ToolFailureKind.TIMEOUT
    assert result.retryable is True
    assert result.error_code == "tool_timeout"


def test_browser_failure_is_classified_for_recovery():
    browser = FakeBrowser(navigate_success=False, error_code="navigation_timeout")
    runtime = DefaultToolRuntime(create_browser_tool_registry(), ToolContext(browser=browser))

    result = asyncio.run(
        runtime.execute(
            ToolRequest(
                name="browser.navigate",
                arguments={"url": "https://example.test"},
            )
        )
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.failure_kind is ToolFailureKind.BROWSER
    assert result.retryable is True
    assert result.error_code == "navigation_timeout"


def test_pre_execution_hook_blocks_before_browser_is_touched():
    browser = FakeBrowser()

    def hook(request, tool, arguments):
        return PreExecutionDecision.block("Blocked by policy.")

    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser),
        pre_execution_hook=hook,
    )

    result = asyncio.run(
        runtime.execute(ToolRequest(name="browser.click", arguments={"element_id": "el_123"}))
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.failure_kind is ToolFailureKind.SECURITY
    assert browser.actions == []


def test_pre_execution_hook_exception_is_structured_without_browser_action():
    browser = FakeBrowser()

    def hook(request, tool, arguments):
        raise RuntimeError("hook failed")

    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser),
        pre_execution_hook=hook,
    )

    result = asyncio.run(
        runtime.execute(
            ToolRequest(
                name="browser.navigate",
                arguments={"url": "https://example.test"},
            )
        )
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.failure_kind is ToolFailureKind.INTERNAL
    assert result.error_code == "pre_execution_hook_error"
    assert browser.actions == []


def test_security_policy_exception_blocks_before_browser_action():
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser),
        security_policy=FailingSecurityPolicy(),
    )

    result = asyncio.run(
        runtime.execute(
            ToolRequest(
                name="browser.navigate",
                arguments={"url": "https://example.test"},
            )
        )
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.failure_kind is ToolFailureKind.SECURITY
    assert result.error_code == "security_policy_error"
    assert browser.actions == []


def test_sensitive_fill_pauses_then_executes_after_explicit_confirmation():
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(create_browser_tool_registry(), ToolContext(browser=browser))
    request = ToolRequest(
        name="browser.fill",
        arguments={"element_id": "field_123", "value": "private value"},
    )

    paused = asyncio.run(runtime.execute(request))
    confirmation_id = paused.data["confirmation"]["confirmation_id"]

    assert paused.status is ToolExecutionStatus.PAUSED
    assert paused.failure_kind is ToolFailureKind.SECURITY
    assert paused.error_code == "security_confirmation_required"
    assert "Требуется подтверждение" in paused.message
    assert browser.actions == []
    assert runtime.history[-1].arguments == {
        "element_id": "field_123",
        "value": "[REDACTED]",
    }

    assert runtime.confirm_pending_action(confirmation_id) is True
    result = asyncio.run(runtime.execute(request))
    assert result.success is True
    assert browser.actions == [("fill", "field_123", "private value")]
    assert runtime.history[-1].arguments == {
        "element_id": "field_123",
        "value": "[REDACTED]",
    }


def test_observe_tool_returns_llm_safe_observation():
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=FakeBrowser(), observation_engine=FakeObservationEngine()),
    )

    result = asyncio.run(runtime.execute(ToolRequest(name="browser.observe", arguments={})))
    observation = result.data["observation"]

    assert result.success is True
    assert observation["summary"] == "Synthetic observation."
    assert "html" not in str(observation).lower()
    assert "dom" not in str(observation).lower()


def test_submit_click_pauses_before_browser_action():
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(
            browser=browser,
            observation_engine=FakeObservationEngine("el_submit", "Submit application"),
        ),
    )

    result = asyncio.run(
        runtime.execute(
            ToolRequest(name="browser.click", arguments={"element_id": "el_submit"})
        )
    )

    assert result.status is ToolExecutionStatus.PAUSED
    assert result.failure_kind is ToolFailureKind.SECURITY
    assert result.data["security"]["risk"] == ActionRisk.EXTERNAL_SIDE_EFFECT.value
    assert result.data["confirmation"]["tool_name"] == "browser.click"
    assert browser.actions == []


def test_destructive_click_requires_confirmation_before_browser_action():
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(
            browser=browser,
            observation_engine=FakeObservationEngine("el_delete", "Delete account"),
        ),
    )

    result = asyncio.run(
        runtime.execute(
            ToolRequest(name="browser.click", arguments={"element_id": "el_delete"})
        )
    )

    assert result.status is ToolExecutionStatus.PAUSED
    assert result.data["security"]["risk"] == ActionRisk.DESTRUCTIVE.value
    assert browser.actions == []


def test_llm_supplied_safe_risk_cannot_bypass_security():
    browser = FakeBrowser()
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(
            browser=browser,
            observation_engine=FakeObservationEngine("el_send", "Send message"),
        ),
    )

    result = asyncio.run(
        runtime.execute(
            ToolRequest(
                name="browser.click",
                arguments={"element_id": "el_send"},
                risk=ActionRisk.SAFE,
            )
        )
    )

    assert result.status is ToolExecutionStatus.PAUSED
    assert result.data["security"]["risk"] == ActionRisk.EXTERNAL_SIDE_EFFECT.value
    assert browser.actions == []


class FakeBrowser:
    def __init__(self, navigate_success=True, error_code=None):
        self.actions = []
        self.navigate_success = navigate_success
        self.error_code = error_code

    async def start(self):
        return None

    async def stop(self):
        return None

    async def navigate_to(self, url):
        self.actions.append(("navigate", url))
        return BrowserActionResult(
            action="navigate_to",
            success=self.navigate_success,
            message="Navigation completed." if self.navigate_success else "Navigation failed.",
            url=url,
            title="Fake",
            error_code=self.error_code,
        )

    async def reload(self):
        self.actions.append(("reload",))
        return BrowserActionResult("reload", True, "Reloaded.")

    async def go_back(self):
        self.actions.append(("back",))
        return BrowserActionResult("go_back", True, "Back.")

    async def go_forward(self):
        self.actions.append(("forward",))
        return BrowserActionResult("go_forward", True, "Forward.")

    async def current_state(self):
        return BrowserState(is_started=True, url="https://example.test", title="Fake")

    async def screenshot(self, path=None):
        self.actions.append(("screenshot", path))
        return ScreenshotResult(True, Path("screenshot.png"), "Screenshot captured.")

    async def capture_semantic_snapshot(self):
        raise AssertionError("Observation tool should use ObservationEngine.")

    async def click_by_semantic_id(self, element_id):
        self.actions.append(("click", element_id))
        return BrowserActionResult("click_by_semantic_id", True, "Clicked.")

    async def fill_by_semantic_id(self, element_id, value):
        self.actions.append(("fill", element_id, value))
        return BrowserActionResult("fill_by_semantic_id", True, "Filled.")

    async def press_key(self, key):
        self.actions.append(("press_key", key))
        return BrowserActionResult("press_key", True, "Pressed.")

    async def wait_for_timeout(self, milliseconds):
        self.actions.append(("wait", milliseconds))
        return BrowserActionResult("wait_for_timeout", True, "Waited.")


class FakeObservationEngine:
    def __init__(self, element_id: str | None = None, label: str | None = None):
        self.element_id = element_id
        self.label = label

    async def observe(self):
        elements = []
        if self.element_id and self.label:
            elements.append(
                InteractiveElement(
                    element_id=self.element_id,
                    role="button",
                    accessible_name=self.label,
                    visible_text=self.label,
                )
            )
        return PageObservation(
            url="https://example.test",
            title="Fake",
            summary="Synthetic observation.",
            interactive_elements=elements,
        )


class FailingSecurityPolicy:
    def evaluate(self, request, context):
        raise RuntimeError("policy failed")


@dataclass(frozen=True)
class SlowTool:
    name: str = "slow.tool"
    description: str = "Slow deterministic tool."
    input_schema: ToolInputSchema = ToolInputSchema()
    output_schema: ToolOutputSchema = ToolOutputSchema(
        fields=(ToolFieldSchema("ok", ToolValueType.BOOLEAN, "Success flag."),)
    )
    timeout_seconds: float = 0.01

    async def execute(self, arguments, context):
        await asyncio.sleep(0.1)
        return ToolExecutionOutcome(True, "Done.", {"ok": True})
