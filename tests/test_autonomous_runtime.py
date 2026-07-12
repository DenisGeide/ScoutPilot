import asyncio
import json
from datetime import datetime, timezone

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.browser.types import BrowserActionResult
from scout_pilot.llm import (
    LlmErrorCode,
    LlmProviderError,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
    MockLlmProvider,
    ReasoningEngine,
)
from scout_pilot.memory import HierarchicalMemory
from scout_pilot.models import (
    ExecutionPlan,
    InteractiveElement,
    PageIssue,
    PageIssueCode,
    PageObservation,
    PlanStep,
    RuntimeStatus,
    SemanticSection,
    ToolRequest,
    UserTask,
)
from scout_pilot.runtime import (
    AgentState,
    AutonomousAgentRuntime,
    RuntimeSettings,
    TaskTerminationReason,
)
from scout_pilot.runtime.agent import _page_blocker_decision
from scout_pilot.observation import SemanticObservationEngine
from scout_pilot.tools import DefaultToolRuntime, ToolContext, create_browser_tool_registry
from scout_pilot.tools.types import (
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolFailureKind,
    ToolFieldSchema,
    ToolInputSchema,
    ToolOutputSchema,
    ToolSchema,
    ToolValueType,
)


def test_runtime_runs_mocked_end_to_end_to_completion():
    provider = MockLlmProvider(
        [
            _tool_call_result("test.click", {"target": "primary"}),
            _text_result("Done."),
        ]
    )
    memory = HierarchicalMemory()
    planner = FakePlanningEngine()
    tool_runtime = FakeToolRuntime([_tool_result("test.click", success=True)])
    runtime = _runtime(provider, planner, tool_runtime, memory)

    events = asyncio.run(_collect(runtime.run(UserTask("Click the primary action"))))

    transition_states = [
        event.details["to_state"]
        for event in events
        if event.name == "state_transition"
    ]
    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Done."
    assert tool_runtime.requests == [
        ToolRequest(name="test.click", arguments={"target": "primary"})
    ]
    assert planner.created == 1
    assert "observing" in transition_states
    assert "planning" in transition_states
    assert "reasoning" in transition_states
    assert "executing" in transition_states
    assert "evaluating" in transition_states
    assert all(
        event.details["reason"]
        for event in events
        if event.name == "state_transition"
    )
    assert any(event.name == "reflection_completed" for event in events)
    budget_events = [event for event in events if event.name == "context_budget_applied"]
    assert any(event.details["component"] == "reasoning" for event in budget_events)
    assert all(event.details["metrics"]["after_tokens"] >= 0 for event in budget_events)
    assert events[-1].status is RuntimeStatus.COMPLETED
    memory_summaries = memory.context_summaries(events[0].details["task_id"])
    assert any("Click the primary action" in item for item in memory_summaries)
    assert any("evaluated as success" in item for item in memory_summaries)
    assert "element_id" not in " ".join(memory_summaries)


def test_runtime_retries_retryable_tool_failure_and_revises_plan():
    provider = MockLlmProvider(
        [
            _tool_call_result("test.click", {"target": "primary"}),
            _text_result("Recovered."),
        ]
    )
    planner = FakePlanningEngine()
    tool_runtime = FakeToolRuntime(
        [
            _tool_result(
                "test.click",
                success=False,
                status=ToolExecutionStatus.FAILED,
                retryable=True,
                message="Temporary failure.",
                failure_kind=ToolFailureKind.BROWSER,
            )
        ]
    )
    runtime = _runtime(
        provider,
        planner,
        tool_runtime,
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=3, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Retry once"))))

    transition_states = [
        event.details["to_state"]
        for event in events
        if event.name == "state_transition"
    ]
    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Recovered."
    assert planner.revisions == 1
    assert "retrying" in transition_states
    assert any(event.name == "plan_revised" for event in events)


def test_runtime_stops_at_max_iterations():
    provider = MockLlmProvider(
        [
            _text_result("NEED_OBSERVATION: first pass"),
            _text_result("NEED_OBSERVATION: second pass"),
        ]
    )
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([]),
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=2, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Keep observing"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is False
    assert runtime.last_result.final_state is AgentState.FAILED
    assert runtime.last_result.termination_reason is TaskTerminationReason.MAX_ITERATIONS_EXCEEDED
    assert events[-1].name == "task_failed"
    assert events[-1].details["termination_reason"] == "max_iterations_exceeded"


def test_runtime_marks_unchanged_observation_before_second_reasoning_request():
    provider = MockLlmProvider(
        [
            _text_result("NEED_OBSERVATION: verify current results"),
            _text_result("Enough evidence is visible."),
        ]
    )
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([]),
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=3, max_failures=1),
        observation_engine=StaticObservationEngine(),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Read stable results"))))
    second_payload = json.loads(provider.requests[1].messages[1].content)

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert any(
        "semantic observation is unchanged" in summary.casefold()
        for summary in second_payload["memory_summaries"]
    )
    assert sum(event.name == "observation_captured" for event in events) == 2


def test_runtime_replans_when_semantic_element_disappears():
    provider = MockLlmProvider(
        [
            _tool_call_result("test.click", {"target": "primary"}),
            _text_result("Recovered after replanning."),
        ]
    )
    planner = FakePlanningEngine()
    tool_runtime = FakeToolRuntime(
        [
            _tool_result(
                "test.click",
                success=False,
                status=ToolExecutionStatus.FAILED,
                retryable=False,
                message="Button disappeared.",
                failure_kind=ToolFailureKind.BROWSER,
                error_code="semantic_element_not_found",
            )
        ]
    )
    runtime = _runtime(
        provider,
        planner,
        tool_runtime,
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=3, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Click missing element"))))
    tool_event = next(event for event in events if event.name == "tool_execution_finished")
    reflection = next(event for event in events if event.name == "reflection_completed")

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Recovered after replanning."
    assert planner.revisions == 1
    assert tool_event.details["success"] is False
    assert tool_event.details["error_code"] == "semantic_element_not_found"
    assert reflection.details["recommended_action"] == "replan"


def test_runtime_stops_when_retryable_failure_reaches_max_failures():
    provider = MockLlmProvider([_tool_call_result("test.click", {"target": "primary"})])
    tool_runtime = FakeToolRuntime(
        [
            _tool_result(
                "test.click",
                success=False,
                status=ToolExecutionStatus.FAILED,
                retryable=True,
                message="Repeated timeout.",
                failure_kind=ToolFailureKind.TIMEOUT,
                error_code="tool_timeout",
            )
        ]
    )
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        tool_runtime,
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=3, max_failures=1),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Click with failure limit"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is False
    assert runtime.last_result.termination_reason is TaskTerminationReason.MAX_FAILURES_EXCEEDED
    assert events[-1].details["termination_reason"] == "max_failures_exceeded"


def test_runtime_reports_provider_failure_with_russian_user_message():
    provider = MockLlmProvider(
        [
            LlmProviderResult(
                success=False,
                error=LlmProviderError(
                    code=LlmErrorCode.TIMEOUT,
                    message="Provider timed out.",
                    retryable=True,
                ),
            )
        ]
    )
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([]),
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=2, max_failures=1),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Handle provider failure"))))
    reasoning_event = next(event for event in events if event.name == "reasoning_completed")

    assert runtime.last_result is not None
    assert runtime.last_result.termination_reason is TaskTerminationReason.REASONING_FAILURE
    assert reasoning_event.details["provider_error"]["code"] == "timeout"
    assert "LLM" in events[-1].details["message_ru"]
    assert "Проверьте" in events[-1].details["message_ru"]


def test_runtime_observes_again_after_noop_without_consuming_failure_limit():
    provider = MockLlmProvider(
        [
            _tool_call_result("test.click", {"target": "primary"}),
            _text_result("Done after no-op check."),
        ]
    )
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([_tool_result("test.click", success=True)]),
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=3, max_failures=1),
        observation_engine=StaticObservationEngine(),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Click a no-op control"))))
    reflection = next(event for event in events if event.name == "reflection_completed")

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Done after no-op check."
    assert reflection.details["outcome"] == "uncertain"
    assert reflection.details["recommended_action"] == "observe_again"
    assert reflection.details["metrics"]["consecutive_no_progress_count"] == 1


def test_runtime_stops_on_captcha_blocker_before_provider_or_tools():
    provider = MockLlmProvider([_tool_call_result("test.click", {"target": "primary"})])
    tool_runtime = FakeToolRuntime([])
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        tool_runtime,
        HierarchicalMemory(),
        observation_engine=BlockedObservationEngine(PageIssueCode.CAPTCHA_BLOCKING_PAGE),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Continue past blocker"))))
    blocker_event = next(event for event in events if event.name == "page_blocker_detected")

    assert runtime.last_result is not None
    assert runtime.last_result.success is False
    assert runtime.last_result.termination_reason is TaskTerminationReason.PAGE_BLOCKER
    assert blocker_event.details["blocker_type"] == "captcha_blocking_page"
    assert blocker_event.details["stop"] is True
    assert events[-1].name == "task_failed"
    assert provider.requests == []
    assert tool_runtime.requests == []


def test_runtime_records_region_prompt_without_treating_it_as_captcha():
    provider = MockLlmProvider([_text_result("Region prompt noted.")])
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([]),
        HierarchicalMemory(),
        observation_engine=BlockedObservationEngine(PageIssueCode.REGION_PROMPT),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Read page with region prompt"))))
    blocker_event = next(event for event in events if event.name == "page_blocker_detected")

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Region prompt noted."
    assert blocker_event.details["blocker_type"] == "region_prompt"
    assert blocker_event.details["stop"] is False
    assert blocker_event.details["requires_user_input"] is True
    assert provider.requests


def test_runtime_does_not_treat_background_loading_as_empty_when_content_is_useful():
    observation = PageObservation(
        url="https://example.test/results",
        title="Search results",
        summary="Three results are visible.",
        sections=[
            SemanticSection(
                section_id="section_results",
                role="main",
                heading="Results",
                text="AI Engineer, Python AI Developer, LLM Engineer",
            )
        ],
        interactive_elements=[
            InteractiveElement(
                element_id="el_result",
                role="link",
                accessible_name="AI Engineer",
                visible_text="AI Engineer",
            )
        ],
        issues=[PageIssue(PageIssueCode.LOADING, "Background requests are active.")],
    )

    assert _page_blocker_decision(observation) is None


def test_runtime_waits_for_confirmation_when_reasoning_requests_it():
    provider = MockLlmProvider([_text_result("NEED_CONFIRMATION: Submit form.")])
    runtime = _runtime(provider, FakePlanningEngine(), FakeToolRuntime([]), HierarchicalMemory())

    events = asyncio.run(_collect(runtime.run(UserTask("Submit the form"))))

    assert runtime.last_result is not None
    assert runtime.last_result.status is RuntimeStatus.WAITING_FOR_CONFIRMATION
    assert runtime.last_result.final_state is AgentState.WAITING_FOR_CONFIRMATION
    assert events[-1].name == "confirmation_required"
    assert "Нужно подтверждение пользователя" in events[-1].details["message_ru"]
    assert "Submit form" not in events[-1].details["message_ru"]


def test_runtime_pauses_and_resumes_security_confirmation():
    provider = MockLlmProvider(
        [
            _tool_call_result("browser.click", {"element_id": "el_submit"}),
            _tool_call_result("browser.click", {"element_id": "el_submit"}),
            _text_result("Done after confirmation."),
        ]
    )
    browser = SecurityFakeBrowser()
    observer = SecurityObservationEngine(browser)
    registry = create_browser_tool_registry()
    runtime = AutonomousAgentRuntime(
        observation_engine=observer,
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(
            tool_request=ToolRequest(
                name="browser.click",
                arguments={"element_id": "el_submit"},
            )
        ),
        tool_runtime=DefaultToolRuntime(
            registry,
            ToolContext(browser=browser, observation_engine=observer),
        ),
        memory=HierarchicalMemory(),
        tool_schemas=registry.schemas(),
        settings=RuntimeSettings(max_iterations=3, max_failures=1),
    )

    paused_events = asyncio.run(_collect(runtime.run(UserTask("Submit the form"))))
    confirmation = runtime.pending_confirmation
    confirmation_id = confirmation["confirmation_id"]

    assert runtime.last_result is not None
    assert runtime.last_result.final_state is AgentState.WAITING_FOR_CONFIRMATION
    assert paused_events[-1].name == "confirmation_required"
    assert "Требуется подтверждение" in runtime.last_result.message
    assert browser.actions == []

    assert runtime.confirm_pending_action(str(confirmation_id)) is True
    resumed_events = asyncio.run(_collect(runtime.run(UserTask("Submit the form"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Done after confirmation."
    assert browser.actions == [("click", "el_submit")]
    assert resumed_events[-1].status is RuntimeStatus.COMPLETED


def test_runtime_cancels_cleanly_before_actions():
    provider = MockLlmProvider([_text_result("Done.")])
    tool_runtime = FakeToolRuntime([])
    runtime = _runtime(provider, FakePlanningEngine(), tool_runtime, HierarchicalMemory())
    runtime.cancel("User cancelled from CLI.")

    events = asyncio.run(_collect(runtime.run(UserTask("Stop immediately"))))

    assert runtime.last_result is not None
    assert runtime.last_result.status is RuntimeStatus.CANCELLED
    assert runtime.last_result.final_state is AgentState.CANCELLED
    assert runtime.last_result.message == "User cancelled from CLI."
    assert events[-1].name == "task_cancelled"
    assert tool_runtime.requests == []
    assert provider.requests == []


def test_runtime_integrates_browser_observation_and_tool_runtime_on_local_page(tmp_path):
    page_path = tmp_path / "runtime-page.html"
    page_path.write_text(
        """
        <!doctype html>
        <title>Before click</title>
        <main>
          <h1>Runtime local page</h1>
          <button aria-label="Finish task" onclick="document.title = 'After click'">Finish</button>
        </main>
        """,
        encoding="utf-8",
    )
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=tmp_path / "screenshots",
        )
    )
    observer = SemanticObservationEngine(browser)
    registry = create_browser_tool_registry()

    async def scenario():
        await browser.start()
        try:
            navigation = await browser.navigate_to(page_path.resolve().as_uri())
            assert navigation.success is True
            initial_observation = await observer.observe()
            button = next(
                element
                for element in initial_observation.interactive_elements
                if element.accessible_name == "Finish task"
            )
            request = ToolRequest(
                name="browser.click",
                arguments={"element_id": button.element_id},
            )
            provider = MockLlmProvider(
                [
                    _tool_call_result(request.name, request.arguments),
                    _text_result("Clicked."),
                ]
            )
            runtime = AutonomousAgentRuntime(
                observation_engine=observer,
                reasoning_engine=ReasoningEngine(provider),
                planning_engine=FakePlanningEngine(tool_request=request),
                tool_runtime=DefaultToolRuntime(
                    registry,
                    ToolContext(browser=browser, observation_engine=observer),
                ),
                memory=HierarchicalMemory(),
                tool_schemas=registry.schemas(),
                settings=RuntimeSettings(max_iterations=3, max_failures=1),
            )

            events = await _collect(runtime.run(UserTask("Click Finish task")))
            state = await browser.current_state()
            return runtime, events, state
        finally:
            await browser.stop()

    runtime, events, state = asyncio.run(scenario())

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Clicked."
    assert state.title == "After click"
    assert any(event.name == "tool_execution_finished" for event in events)


async def _collect(stream):
    return [event async for event in stream]


def _runtime(
    provider,
    planner,
    tool_runtime,
    memory,
    settings: RuntimeSettings | None = None,
    observation_engine=None,
):
    return AutonomousAgentRuntime(
        observation_engine=observation_engine or QueuedObservationEngine(),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=planner,
        tool_runtime=tool_runtime,
        memory=memory,
        tool_schemas=[_tool_schema()],
        settings=settings or RuntimeSettings(max_iterations=4, max_failures=1),
        security_constraints=["Ask before external side effects."],
        confirmation_constraints=["Pause before submit actions."],
        budget={"remaining_tokens": 1000},
    )


def _tool_schema() -> ToolSchema:
    return ToolSchema(
        name="test.click",
        description="Click a synthetic semantic element.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema(
                    "target",
                    ToolValueType.STRING,
                    "Synthetic semantic target.",
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _tool_call_result(name, arguments):
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(
            tool_calls=(LlmToolCall(name=name, arguments=arguments),)
        ),
    )


def _text_result(content):
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(content=content),
    )


def _tool_result(
    tool_name: str,
    *,
    success: bool,
    status: ToolExecutionStatus = ToolExecutionStatus.SUCCESS,
    retryable: bool = False,
    message: str = "Tool completed.",
    failure_kind: ToolFailureKind | None = None,
    error_code: str | None = None,
) -> ToolExecutionResult:
    now = datetime.now(tz=timezone.utc)
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status,
        success=success,
        message=message,
        failure_kind=failure_kind,
        retryable=retryable,
        error_code=error_code,
        started_at=now,
        finished_at=now,
    )


class QueuedObservationEngine:
    def __init__(self):
        self.count = 0

    async def observe(self):
        self.count += 1
        return PageObservation(
            url=f"https://example.test/page-{self.count}",
            title=f"Synthetic {self.count}",
            summary=f"Synthetic page {self.count}.",
        )


class StaticObservationEngine:
    async def observe(self):
        return PageObservation(
            url="https://example.test/same",
            title="Same",
            summary="Same semantic state.",
        )


class BlockedObservationEngine:
    def __init__(self, code: PageIssueCode):
        self.code = code

    async def observe(self):
        return PageObservation(
            url="https://example.test/blocked",
            title="Blocked",
            summary=f"Blocked by {self.code.value}.",
            issues=[
                PageIssue(
                    self.code,
                    "Synthetic blocker.",
                    severity="warning",
                )
            ],
        )


class SecurityFakeBrowser:
    def __init__(self):
        self.actions = []

    async def click_by_semantic_id(self, element_id):
        self.actions.append(("click", element_id))
        return BrowserActionResult(
            action="click_by_semantic_id",
            success=True,
            message="Clicked.",
            url="https://example.test",
            title="After click",
        )


class SecurityObservationEngine:
    def __init__(self, browser):
        self.browser = browser

    async def observe(self):
        clicked = bool(self.browser.actions)
        return PageObservation(
            url="https://example.test",
            title="After click" if clicked else "Before click",
            summary="Submitted." if clicked else "Form ready.",
            interactive_elements=[
                InteractiveElement(
                    element_id="el_submit",
                    role="button",
                    accessible_name="Submit form",
                    visible_text="Submit form",
                )
            ],
        )


class FakePlanningEngine:
    def __init__(self, tool_request: ToolRequest | None = None):
        self.created = 0
        self.revisions = 0
        self.memory_summaries = []
        self.tool_request = tool_request or ToolRequest(
            name="test.click",
            arguments={"target": "primary"},
        )

    async def create_plan(
        self,
        task,
        observation,
        memory_summaries=(),
        available_tools=(),
    ):
        self.created += 1
        self.memory_summaries.append(tuple(memory_summaries))
        return ExecutionPlan(
            task=task,
            summary=f"Use {self.tool_request.name}.",
            steps=[
                PlanStep(
                    goal="Execute the selected semantic action.",
                    tool_name=self.tool_request.name,
                    arguments=self.tool_request.arguments,
                    tool_request=self.tool_request,
                )
            ],
            memory_summaries=memory_summaries,
        )

    async def revise_plan(
        self,
        plan,
        observation,
        reason,
        memory_summaries=(),
        available_tools=(),
    ):
        self.revisions += 1
        return ExecutionPlan(
            task=plan.task,
            summary=f"Revised: {reason}",
            steps=plan.steps,
            memory_summaries=memory_summaries,
            revision_reason=reason,
        )


class FakeToolRuntime:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        if not self.results:
            raise AssertionError("FakeToolRuntime has no queued result.")
        return self.results.pop(0)
