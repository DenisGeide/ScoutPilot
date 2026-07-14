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
    DialogSummary,
    FormFieldSummary,
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
from scout_pilot.runtime.agent import (
    _alternative_unvisited_target_tool,
    _answer_observed_resource_count,
    _dominant_interactive_resource_shape,
    _first_unvisited_resource_tool,
    _page_blocker_decision,
    _qualified_resource_probe_target,
    _requested_distinct_resource_count,
    _resource_observation_has_evidence,
    _resource_observation_matches_explicit_evidence,
    _resource_observation_summaries,
    _task_has_hard_numeric_filter,
    _task_requires_qualified_resources,
    _target_url_for_tool,
    _without_upper_bound_search_filter,
)
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
        event.details["to_state"] for event in events if event.name == "state_transition"
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
    assert all(event.details["reason"] for event in events if event.name == "state_transition")
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
        event.details["to_state"] for event in events if event.name == "state_transition"
    ]
    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Recovered."
    assert planner.revisions == 1
    assert "retrying" in transition_states
    assert any(event.name == "plan_revised" for event in events)


def test_runtime_returns_partial_result_at_max_iterations():
    provider = MockLlmProvider(
        [
            _text_result("NEED_OBSERVATION: first pass"),
            _text_result("NEED_OBSERVATION: second pass"),
            _text_result("Partial answer from collected observations."),
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
    assert runtime.last_result.final_state is AgentState.COMPLETED
    assert runtime.last_result.termination_reason is TaskTerminationReason.PARTIAL_RESULT
    assert runtime.last_result.answer == "Partial answer from collected observations."
    assert events[-1].name == "task_partial_result"
    assert events[-1].details["termination_reason"] == "partial_result"


def test_runtime_returns_partial_result_when_wall_clock_budget_is_exhausted():
    provider = MockLlmProvider([_text_result("Partial answer within the video budget.")])
    observation_engine = QueuedObservationEngine()
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([]),
        HierarchicalMemory(),
        settings=RuntimeSettings(
            max_iterations=128,
            max_failures=2,
            max_elapsed_seconds=0,
        ),
        observation_engine=observation_engine,
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Return collected work on timeout"))))

    assert runtime.last_result is not None
    assert runtime.last_result.termination_reason is TaskTerminationReason.PARTIAL_RESULT
    assert runtime.last_result.answer == "Partial answer within the video budget."
    assert observation_engine.count == 0
    assert events[-1].details["completion_trigger"] == "max_elapsed_seconds"


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


def test_runtime_safely_dismisses_unrelated_modal_before_reasoning():
    provider = MockLlmProvider([_text_result("The useful page is visible.")])
    observation_engine = DismissibleModalObservationEngine()
    tool_runtime = ModalDismissToolRuntime(observation_engine)
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        tool_runtime,
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=2, max_failures=1),
        observation_engine=observation_engine,
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Read the vacancy list"))))
    provider_payload = json.loads(provider.requests[0].messages[1].content)

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [
        ToolRequest(name="browser.press_key", arguments={"key": "Escape"})
    ]
    assert any(event.name == "modal_dismiss_started" for event in events)
    finished = next(event for event in events if event.name == "modal_dismiss_finished")
    assert finished.details["dismissed"] is True
    assert "dialogs" not in provider_payload["observation"]
    assert provider_payload["observation"].get("issues", []) == []


def test_runtime_blocks_reopening_the_same_observed_target_url():
    target_url = "https://example.test/vacancies/first"
    provider = MockLlmProvider(
        [
            _tool_call_result("browser.click", {"element_id": "el_first"}),
            _tool_call_result("browser.click", {"element_id": "el_first"}),
            _text_result("Compared distinct vacancies."),
        ]
    )
    planner = FakePlanningEngine(
        ToolRequest(name="browser.click", arguments={"element_id": "el_first"})
    )
    tool_runtime = FakeToolRuntime([_tool_result("browser.click", success=True)])
    runtime = AutonomousAgentRuntime(
        observation_engine=RepeatedLinkObservationEngine(target_url),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=planner,
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_browser_click_schema()],
        settings=RuntimeSettings(max_iterations=4, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Compare different vacancies"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Compared distinct vacancies."
    assert len(tool_runtime.requests) == 1
    blocked = next(event for event in events if event.name == "repeated_target_blocked")
    assert blocked.details["target_url"] == target_url


def test_runtime_blocks_repeating_exact_tool_request_on_unchanged_page():
    request = ToolRequest(name="test.click", arguments={"target": "search"})
    provider = MockLlmProvider(
        [
            _tool_call_result(request.name, request.arguments),
            _tool_call_result(request.name, request.arguments),
            _text_result("Used an alternative and completed."),
        ]
    )
    tool_runtime = FakeToolRuntime([_tool_result(request.name, success=True)])
    runtime = AutonomousAgentRuntime(
        observation_engine=StaticObservationEngine(),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(request),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_tool_schema()],
        settings=RuntimeSettings(max_iterations=4, max_failures=3),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Use the search once"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [request]
    blocked = next(event for event in events if event.name == "repeated_tool_request_blocked")
    assert blocked.details["tool_name"] == request.name
    assert blocked.details["next_action"] == "choose_different_tool_or_more_specific_target"


def test_runtime_executes_a_resolved_click_instead_of_resolving_it_again():
    target_url = "https://example.test/vacancies/1001"
    resolve_request = ToolRequest(
        name="browser.resolve_target",
        arguments={"kind": "click", "target": "AI Engineer", "role": "link"},
    )

    class ObservationEngine:
        opened = False

        async def observe(self):
            if self.opened:
                return PageObservation(
                    url=target_url,
                    title="AI Engineer",
                    summary="Vacancy requirements.",
                )
            return PageObservation(
                url="https://example.test/search",
                title="Search results",
                summary="Vacancy search results.",
                interactive_elements=[
                    InteractiveElement(
                        element_id="el_ai_engineer",
                        role="link",
                        accessible_name="AI Engineer",
                        visible_text="AI Engineer",
                        target_url=target_url,
                    )
                ],
            )

    observation_engine = ObservationEngine()

    class Runtime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            if request.name == "browser.resolve_target":
                return _tool_result(
                    request.name,
                    success=True,
                    data={
                        "resolution": {
                            "status": "resolved",
                            "selected": {
                                "id": "el_ai_engineer",
                                "target_url": target_url,
                            },
                        }
                    },
                )
            observation_engine.opened = True
            return _tool_result(request.name, success=True)

    tool_runtime = Runtime()
    provider = MockLlmProvider(
        [
            _tool_call_result(resolve_request.name, resolve_request.arguments),
            _text_result("Read the vacancy."),
        ]
    )
    runtime = AutonomousAgentRuntime(
        observation_engine=observation_engine,
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(resolve_request),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[
            _browser_resolve_target_schema(),
            _browser_navigate_schema(),
        ],
        settings=RuntimeSettings(max_iterations=4, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Read an AI Engineer vacancy"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [
        resolve_request,
        ToolRequest(name="browser.navigate", arguments={"url": target_url}),
    ]
    assert len(provider.requests) == 2
    assert any(event.name == "resolved_target_followup_selected" for event in events)
    assert not any(event.name == "repeated_tool_request_blocked" for event in events)


def test_runtime_recovers_failed_semantic_match_without_another_provider_decision():
    result_urls = [
        "https://example.test/vacancies/1001",
        "https://example.test/vacancies/1002",
        "https://example.test/vacancies/1003",
    ]
    resolve_request = ToolRequest(
        name="browser.resolve_target",
        arguments={"kind": "click", "target": "matching vacancy", "role": "link"},
    )

    class ObservationEngine:
        opened_url = None

        async def observe(self):
            if self.opened_url is not None:
                return PageObservation(
                    url=self.opened_url,
                    title="AI Engineer",
                    summary="Python and LLM requirements.",
                    sections=[
                        SemanticSection(
                            section_id="requirements",
                            role="main",
                            heading="Requirements",
                            text="Production Python and LLM application experience.",
                        )
                    ],
                )
            return PageObservation(
                url="https://example.test/search",
                title="Search results",
                summary="Three vacancy results.",
                interactive_elements=[
                    InteractiveElement(
                        element_id=f"el_{index}",
                        role="link",
                        accessible_name=f"Vacancy {index}",
                        visible_text=f"Vacancy {index}",
                        target_url=url,
                    )
                    for index, url in enumerate(result_urls, start=1)
                ],
            )

    observation_engine = ObservationEngine()

    class Runtime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            if request.name == "browser.resolve_target":
                return _tool_result(
                    request.name,
                    success=False,
                    status=ToolExecutionStatus.FAILED,
                    message="Multiple visible semantic candidates matched the intent.",
                    error_code="ambiguous_semantic_target",
                )
            observation_engine.opened_url = request.arguments["url"]
            return _tool_result(request.name, success=True)

    tool_runtime = Runtime()
    provider = MockLlmProvider(
        [
            _tool_call_result(resolve_request.name, resolve_request.arguments),
            _text_result("One candidate was read; continue from the collected evidence."),
        ]
    )
    runtime = AutonomousAgentRuntime(
        observation_engine=observation_engine,
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(resolve_request),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[
            _browser_resolve_target_schema(),
            _browser_navigate_schema(),
        ],
        settings=RuntimeSettings(max_iterations=4, max_failures=2),
    )

    events = asyncio.run(
        _collect(runtime.run(UserTask("Find three different vacancies and compare them")))
    )

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [
        resolve_request,
        ToolRequest(name="browser.navigate", arguments={"url": result_urls[0]}),
    ]
    assert len(provider.requests) == 2
    assert any(event.name == "semantic_recovery_scheduled" for event in events)
    assert any(event.name == "semantic_recovery_selected" for event in events)


def test_filtered_collection_continues_until_three_observed_urls_are_answered():
    urls = [
        "https://example.test/vacancies/1001",
        "https://example.test/vacancies/1002",
        "https://example.test/vacancies/1003",
    ]

    class ObservationEngine:
        current_url = urls[0]

        async def observe(self):
            return PageObservation(
                url=self.current_url,
                title=f"AI Engineer {self.current_url.rsplit('/', 1)[-1]}",
                summary="Salary 300000 RUB. Production Python and LLM role.",
                sections=[
                    SemanticSection(
                        section_id="requirements",
                        role="main",
                        heading="Requirements",
                        text="Salary 300000 RUB. Python, LLM and RAG experience required.",
                    )
                ],
                interactive_elements=[
                    InteractiveElement(
                        element_id=f"el_{index}",
                        role="link",
                        accessible_name=f"AI Engineer {index}",
                        visible_text=f"AI Engineer {index}",
                        target_url=url,
                    )
                    for index, url in enumerate(urls, start=1)
                    if url != self.current_url
                ],
            )

    observation_engine = ObservationEngine()

    class Runtime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            observation_engine.current_url = str(request.arguments["url"])
            return _tool_result(request.name, success=True)

    provider = MockLlmProvider([_text_result(f"Three matches. {urls[0]} {urls[1]} {urls[2]}")])
    tool_runtime = Runtime()
    runtime = AutonomousAgentRuntime(
        observation_engine=observation_engine,
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_browser_navigate_schema()],
        settings=RuntimeSettings(max_iterations=6, max_failures=2),
        initial_memory_summaries=("Previous turn selected only verified links.",),
    )

    events = asyncio.run(
        _collect(
            runtime.run(
                UserTask("Find three vacancies with explicitly stated salary and requirements")
            )
        )
    )

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == f"Three matches. {urls[0]} {urls[1]} {urls[2]}"
    assert tool_runtime.requests == [
        ToolRequest("browser.navigate", {"url": urls[1]}),
        ToolRequest("browser.navigate", {"url": urls[2]}),
    ]
    assert len(provider.requests) == 1
    assert (
        len([event for event in events if event.name == "resource_collection_followup_selected"])
        == 2
    )
    assert runtime.last_observed_resource_urls == tuple(urls)
    first_payload = json.loads(provider.requests[0].messages[1].content)
    assert "Previous turn selected only verified links." in first_payload["memory_summaries"]


def test_comparison_followup_uses_conversation_memory_without_browsing():
    urls = (
        "https://example.test/vacancies/1001",
        "https://example.test/vacancies/1002",
        "https://example.test/vacancies/1003",
    )
    provider = MockLlmProvider([_text_result(f"Лучший вариант — AI Engineer\n{urls[1]}")])
    tool_runtime = FakeToolRuntime([])
    runtime = AutonomousAgentRuntime(
        observation_engine=StaticObservationEngine(),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_browser_navigate_schema()],
        initial_memory_summaries=(
            f"Previous verified results: {' '.join(urls)}",
            "All three pages were read separately.",
        ),
    )

    events = asyncio.run(
        _collect(
            runtime.run(UserTask("Compare the previously found results and choose the best option"))
        )
    )

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == f"Лучший вариант — AI Engineer\n{urls[1]}"
    assert len(provider.requests) == 1
    assert tool_runtime.requests == []
    completed = next(event for event in events if event.name == "task_completed")
    assert completed.details["completion_trigger"] == "memory_only_followup"


def test_qualified_resource_helpers_require_observed_answer_urls():
    urls = {
        "https://example.test/vacancies/1001",
        "https://example.test/vacancies/1002",
    }

    assert _task_requires_qualified_resources(
        "Найди три вакансии с явно указанной зарплатой и требованиями"
    )
    assert _qualified_resource_probe_target("Find three jobs with salary", 3) == 5
    assert (
        _answer_observed_resource_count(
            "One observed https://example.test/vacancies/1001 and one invented "
            "https://example.test/vacancies/9999",
            urls,
        )
        == 1
    )


def test_resource_collection_keeps_the_dominant_resource_shape():
    vacancies = [
        "https://example.test/vacancy/1001",
        "https://example.test/vacancy/1002",
        "https://example.test/vacancy/1003",
    ]
    observation = PageObservation(
        url="https://example.test/search",
        title="Results",
        summary="Three vacancies and service links are visible.",
        interactive_elements=[
            *[
                InteractiveElement(
                    element_id=f"vacancy_{index}",
                    role="link",
                    accessible_name=f"Vacancy {index}",
                    visible_text=f"Vacancy {index}",
                    target_url=url,
                )
                for index, url in enumerate(vacancies, start=1)
            ],
            InteractiveElement(
                element_id="employer",
                role="link",
                accessible_name="Employer",
                visible_text="Employer",
                target_url="https://example.test/employer/1455",
            ),
            InteractiveElement(
                element_id="help",
                role="link",
                accessible_name="Help",
                visible_text="Help",
                target_url="https://example.test/article/5951",
            ),
        ],
    )

    preferred_shape = _dominant_interactive_resource_shape(observation)
    request = _first_unvisited_resource_tool(
        observation,
        {vacancies[0]},
        preferred_shape=preferred_shape,
    )

    assert preferred_shape == ("https", "example.test", "/vacancy/{id}")
    assert request == ToolRequest("browser.navigate", {"url": vacancies[1]})


def test_explicit_salary_evidence_requires_a_visible_numeric_value():
    missing_salary = PageObservation(
        url="https://example.test/vacancy/1001",
        title="AI Engineer",
        summary="Salary not specified.",
        sections=[
            SemanticSection(
                section_id="main",
                role="main",
                heading="Requirements",
                text="Python and LLM production experience required.",
            )
        ],
    )
    visible_salary = PageObservation(
        url="https://example.test/vacancy/1002",
        title="AI Engineer",
        summary="Salary 300 000 RUB per month.",
        sections=missing_salary.sections,
    )

    task = "Find vacancies with explicitly stated salary"
    assert _resource_observation_matches_explicit_evidence(task, missing_salary) is False
    assert _resource_observation_matches_explicit_evidence(task, visible_salary) is True


def test_explicit_salary_evidence_enforces_upper_and_lower_bounds():
    def observation(summary: str, resource_id: int) -> PageObservation:
        return PageObservation(
            url=f"https://example.test/vacancy/{resource_id}",
            title="AI Engineer",
            summary=summary,
            sections=[
                SemanticSection(
                    section_id="main",
                    role="main",
                    heading="Requirements",
                    text="Python, LLM and RAG production experience required.",
                )
            ],
        )

    up_to_task = "Найди вакансии с зарплатой до 70 000 рублей"
    assert (
        _resource_observation_matches_explicit_evidence(
            up_to_task,
            observation("Зарплата до 60 000 ₽ в месяц.", 1001),
        )
        is True
    )
    assert (
        _resource_observation_matches_explicit_evidence(
            up_to_task,
            observation("Зарплата до 150 000 ₽ в месяц.", 1002),
        )
        is False
    )
    assert (
        _resource_observation_matches_explicit_evidence(
            up_to_task,
            observation("Зарплата 250 000–350 000 ₽ в месяц.", 1003),
        )
        is False
    )

    at_least_task = "Find vacancies with salary at least 250000 RUB"
    assert (
        _resource_observation_matches_explicit_evidence(
            at_least_task,
            observation("Salary from 300000 RUB per month.", 1004),
        )
        is True
    )
    assert (
        _resource_observation_matches_explicit_evidence(
            at_least_task,
            observation("Salary 200000-350000 RUB per month.", 1005),
        )
        is False
    )


def test_runtime_opens_an_unvisited_result_instead_of_rewriting_search_repeatedly():
    target_url = "https://example.test/items/1001"
    search_requests = [
        ToolRequest(
            name="browser.fill_by_label",
            arguments={"label": "Search", "value": value},
        )
        for value in ("AI Engineer", "Python AI Developer", "LLM Engineer")
    ]

    class ObservationEngine:
        opened = False

        async def observe(self):
            if self.opened:
                return PageObservation(
                    url=target_url,
                    title="AI Engineer",
                    summary="A result detail page.",
                )
            return PageObservation(
                url="https://example.test/search",
                title="Search results",
                summary="Search results with an unvisited item.",
                interactive_elements=[
                    InteractiveElement(
                        element_id="el_result",
                        role="link",
                        accessible_name="AI Engineer",
                        visible_text="AI Engineer",
                        target_url=target_url,
                    )
                ],
            )

    observation_engine = ObservationEngine()

    class Runtime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            if request.name == "browser.navigate":
                observation_engine.opened = True
            return _tool_result(request.name, success=True)

    tool_runtime = Runtime()
    provider = MockLlmProvider(
        [
            *(_tool_call_result(request.name, request.arguments) for request in search_requests),
            _text_result("Read one unvisited result."),
        ]
    )
    runtime = AutonomousAgentRuntime(
        observation_engine=observation_engine,
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(search_requests[0]),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[
            _browser_fill_by_label_schema(),
            _browser_navigate_schema(),
        ],
        settings=RuntimeSettings(
            max_iterations=5,
            max_failures=2,
            max_search_reformulations=2,
        ),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Compare search results"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [
        search_requests[0],
        search_requests[1],
        ToolRequest(name="browser.navigate", arguments={"url": target_url}),
    ]
    assert any(event.name == "search_reformulation_redirected" for event in events)


def test_runtime_submits_a_search_fill_without_another_provider_decision():
    fill_request = ToolRequest(
        name="browser.fill_by_label",
        arguments={"label": "Search vacancies", "value": "AI Engineer"},
    )

    class SearchObservationEngine:
        async def observe(self):
            return PageObservation(
                url="https://example.test/search",
                title="Vacancy search",
                summary="Search vacancies.",
                form_fields=[
                    FormFieldSummary(
                        field_id="field_search",
                        role="searchbox",
                        input_type="search",
                        label="Search vacancies",
                        placeholder="Role",
                        value_state="filled",
                    )
                ],
            )

    provider = MockLlmProvider(
        [
            _tool_call_result(fill_request.name, fill_request.arguments),
            _text_result("Search submitted and results inspected."),
        ]
    )
    tool_runtime = FakeToolRuntime(
        [
            _tool_result("browser.fill_by_label", success=True),
            _tool_result("browser.press_key", success=True),
        ]
    )
    runtime = AutonomousAgentRuntime(
        observation_engine=SearchObservationEngine(),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(fill_request),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[
            _browser_fill_by_label_schema(),
            _browser_press_key_schema(),
        ],
        settings=RuntimeSettings(max_iterations=4, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Find AI Engineer vacancies"))))

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [
        fill_request,
        ToolRequest(name="browser.press_key", arguments={"key": "Enter"}),
    ]
    assert len(provider.requests) == 2
    assert any(event.name == "search_submit_selected" for event in events)


def test_upper_bound_is_removed_from_generic_search_query():
    observation = PageObservation(
        url="https://example.test/search",
        title="Search",
        summary="Search form.",
    )
    request = ToolRequest(
        name="browser.fill_by_label",
        arguments={
            "label": "Поиск вакансий",
            "value": "AI Engineer зарплата до 70 000 рублей",
        },
    )

    sanitized, changed = _without_upper_bound_search_filter(
        "Найди AI Engineer с зарплатой до 70 000 рублей",
        observation,
        request,
    )

    assert changed is True
    assert sanitized.arguments["value"] == "AI Engineer"


def test_lower_bound_search_query_is_not_rewritten_as_an_upper_bound():
    observation = PageObservation(
        url="https://example.test/search",
        title="Search",
        summary="Search form.",
    )
    request = ToolRequest(
        name="browser.fill_by_label",
        arguments={
            "label": "Поиск вакансий",
            "value": "AI Engineer зарплата от 250 000 рублей",
        },
    )

    sanitized, changed = _without_upper_bound_search_filter(
        "Найди AI Engineer с зарплатой от 250 000 рублей",
        observation,
        request,
    )

    assert changed is False
    assert sanitized == request


def test_runtime_tracks_urls_opened_through_semantic_click_intent():
    target_url = "https://example.test/vacancies/first"
    request = ToolRequest(
        name="browser.click_by_intent",
        arguments={"target": "First vacancy", "role": "link"},
    )
    provider = MockLlmProvider(
        [
            _tool_call_result(request.name, request.arguments),
            _tool_call_result(request.name, request.arguments),
            _text_result("Compared distinct vacancies."),
        ]
    )
    tool_runtime = FakeToolRuntime([_tool_result("browser.click_by_intent", success=True)])
    runtime = AutonomousAgentRuntime(
        observation_engine=RepeatedLinkObservationEngine(target_url),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(request),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_browser_click_by_intent_schema()],
        settings=RuntimeSettings(max_iterations=4, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Compare different vacancies"))))
    second_reasoning_payload = json.loads(provider.requests[1].messages[1].content)

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [request]
    assert second_reasoning_payload["visited_target_urls"] == [target_url]
    blocked = next(event for event in events if event.name == "repeated_target_blocked")
    assert blocked.details["target_url"] == target_url


def test_runtime_infers_link_url_for_semantic_button_with_the_same_target():
    target_url = "https://example.test/vacancies/1001?source=results"
    observation = PageObservation(
        url="https://example.test/results",
        title="Results",
        summary="A card exposes both a button and a link.",
        interactive_elements=[
            InteractiveElement(
                element_id="el_button",
                role="button",
                accessible_name="AI Engineer",
                visible_text="AI Engineer",
            ),
            InteractiveElement(
                element_id="el_link",
                role="link",
                accessible_name="AI Engineer",
                visible_text="AI Engineer",
                target_url=target_url,
            ),
        ],
    )
    request = ToolRequest(
        name="browser.click_by_intent",
        arguments={"target": "AI Engineer", "role": "button"},
    )

    assert _target_url_for_tool(observation, request) == target_url


def test_runtime_remaps_repeated_resource_url_to_unvisited_equivalent():
    first_url = "https://example.test/items/1001?source=results"
    second_url = "https://example.test/items/1002?source=results"
    provider = MockLlmProvider(
        [
            _tool_call_result("browser.navigate", {"url": first_url}),
            _tool_call_result("browser.navigate", {"url": first_url}),
            _text_result("Compared two different items."),
        ]
    )
    planner = FakePlanningEngine(ToolRequest(name="browser.navigate", arguments={"url": first_url}))
    tool_runtime = FakeToolRuntime(
        [
            _tool_result("browser.navigate", success=True),
            _tool_result("browser.navigate", success=True),
        ]
    )
    runtime = AutonomousAgentRuntime(
        observation_engine=MultipleResourceObservationEngine(first_url, second_url),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=planner,
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_browser_navigate_schema()],
        settings=RuntimeSettings(max_iterations=4, max_failures=1),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Compare different result pages"))))
    second_reasoning_payload = json.loads(provider.requests[1].messages[1].content)

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert tool_runtime.requests == [
        ToolRequest(name="browser.navigate", arguments={"url": first_url}),
        ToolRequest(name="browser.navigate", arguments={"url": second_url}),
    ]
    remapped = next(event for event in events if event.name == "repeated_target_remapped")
    assert remapped.details["original_target_url"] == first_url
    assert remapped.details["target_url"] == second_url
    assert second_reasoning_payload["visited_target_urls"] == [first_url]
    interactive = second_reasoning_payload["observation"]["interactive_elements"]
    assert interactive[0]["target_url"] == second_url
    assert any(
        item.get("accessible_name", "").startswith("[already visited]")
        for item in interactive
        if item.get("target_url") == first_url
    )


def test_semantic_click_intent_can_remap_to_an_unvisited_equivalent_url():
    first_url = "https://example.test/items/1001"
    second_url = "https://example.test/items/1002"
    observation = PageObservation(
        url="https://example.test/search",
        title="Results",
        summary="Two equivalent result links.",
        interactive_elements=[
            InteractiveElement(
                element_id="el_first",
                role="link",
                accessible_name="AI Engineer",
                visible_text="AI Engineer",
                target_url=first_url,
            ),
            InteractiveElement(
                element_id="el_second",
                role="link",
                accessible_name="Python AI Developer",
                visible_text="Python AI Developer",
                target_url=second_url,
            ),
        ],
    )

    remapped = _alternative_unvisited_target_tool(
        observation,
        ToolRequest(
            name="browser.click_by_intent",
            arguments={"target": "AI Engineer", "role": "link"},
        ),
        first_url,
        {first_url},
    )

    assert remapped == ToolRequest(
        name="browser.navigate",
        arguments={"url": second_url},
    )


def test_runtime_finalizes_immediately_after_requested_resource_count():
    resource_urls = [
        "https://example.test/items/1001",
        "https://example.test/items/1002",
        "https://example.test/items/1003",
    ]
    provider = MockLlmProvider(
        [
            _tool_call_result("browser.navigate", {"url": resource_urls[0]}),
            _text_result("Three distinct resources compared with exact links."),
        ]
    )
    tool_runtime = FakeToolRuntime(
        [
            _tool_result("browser.navigate", success=True),
            _tool_result("browser.back", success=True),
            _tool_result("browser.navigate", success=True),
            _tool_result("browser.back", success=True),
            _tool_result("browser.navigate", success=True),
        ]
    )
    runtime = AutonomousAgentRuntime(
        observation_engine=FastMultiResourceObservationEngine(resource_urls),
        reasoning_engine=ReasoningEngine(provider),
        planning_engine=FakePlanningEngine(
            ToolRequest(name="browser.navigate", arguments={"url": resource_urls[0]})
        ),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=[_browser_navigate_schema(), _browser_back_schema()],
        settings=RuntimeSettings(max_iterations=128, max_failures=5),
    )

    events = asyncio.run(
        _collect(
            runtime.run(UserTask("Найди три разные страницы, прочитай каждую и сравни результаты"))
        )
    )
    final_payload = json.loads(provider.requests[-1].messages[1].content)

    assert runtime.last_result is not None
    assert runtime.last_result.success is True
    assert runtime.last_result.answer == "Three distinct resources compared with exact links."
    assert len(provider.requests) == 2
    assert len(tool_runtime.requests) == 5
    assert provider.requests[-1].tools == ()
    assert final_payload["final_answer_only"] is True
    assert len(final_payload["visited_target_urls"]) == 3
    memory_text = " ".join(final_payload["memory_summaries"])
    assert "Verified requirements for resource 1" in memory_text
    assert "Verified requirements for resource 2" in memory_text
    assert "Verified requirements for resource 3" in memory_text
    completed = next(event for event in events if event.name == "task_completed")
    assert completed.details["completion_trigger"] == "requested_resource_count_reached"
    assert completed.details["completed_resource_count"] == 3


def test_requested_resource_count_ignores_salary_and_experience_numbers():
    assert (
        _requested_distinct_resource_count(
            "Найди три разные вакансии с зарплатой 250000 и опытом 5 лет"
        )
        == 3
    )
    assert (
        _requested_distinct_resource_count("Найди вакансии с зарплатой 10 долларов и опытом 5 лет")
        is None
    )


def test_hard_numeric_filter_disables_unqualified_resource_count_finalization():
    assert _task_has_hard_numeric_filter("Найди три вакансии с зарплатой до 70 000 рублей")
    assert _task_has_hard_numeric_filter("Find three jobs paying at least 250000 RUB")
    assert not _task_has_hard_numeric_filter("Найди три разные вакансии и сравни требования")


def test_resource_completion_requires_semantic_page_evidence():
    url = "https://example.test/items/1001"
    metadata_only = PageObservation(
        url=url,
        title="Resource 1",
        summary="Resource URL opened.",
    )
    with_details = PageObservation(
        url=url,
        title="Resource 1",
        summary="Resource URL opened.",
        sections=[
            SemanticSection(
                section_id="requirements",
                role="main",
                heading="Requirements",
                text="Production Python and reliable LLM integrations are required.",
            )
        ],
    )

    assert _resource_observation_has_evidence(metadata_only) is False
    assert _resource_observation_has_evidence(with_details) is True


def test_resource_memory_preserves_overview_and_requirement_evidence():
    observation = PageObservation(
        url="https://example.test/items/1001?source=search",
        title="Senior AI Engineer",
        summary="Salary 300000 RUB. Experience 3-6 years.",
        sections=[
            SemanticSection(
                section_id="overview",
                role="main",
                heading="Overview",
                text="The product automates document processing for enterprise customers.",
            ),
            SemanticSection(
                section_id="company",
                role="main",
                heading="Company",
                text="The distributed team works across several product areas.",
            ),
            SemanticSection(
                section_id="requirements",
                role="main",
                heading="Requirements",
                text=(
                    "Requirements include production Python, RAG pipelines, PostgreSQL, "
                    "LLM evaluation and API integrations."
                ),
            ),
        ],
    )

    summaries = _resource_observation_summaries(observation)

    assert len(summaries) == 3
    assert "https://example.test/items/1001" in summaries[0]
    assert any("document processing" in summary for summary in summaries)
    assert any("RAG pipelines" in summary for summary in summaries)
    assert all(len(summary) <= 560 for summary in summaries)


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


def test_runtime_returns_partial_result_when_retryable_failure_reaches_limit():
    provider = MockLlmProvider(
        [
            _tool_call_result("test.click", {"target": "primary"}),
            _text_result("Partial result after tool failure."),
        ]
    )
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
    assert runtime.last_result.termination_reason is TaskTerminationReason.PARTIAL_RESULT
    assert runtime.last_result.answer == "Partial result after tool failure."
    assert events[-1].details["termination_reason"] == "partial_result"


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
    assert runtime.last_result.termination_reason is TaskTerminationReason.PARTIAL_RESULT
    assert reasoning_event.details["provider_error"]["code"] == "timeout"
    assert runtime.last_result.answer is not None
    assert "защит" in events[-1].details["message_ru"].casefold()


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


def test_runtime_stops_on_browser_observation_error_before_provider_or_tools():
    provider = MockLlmProvider([_tool_call_result("test.click", {"target": "primary"})])
    tool_runtime = FakeToolRuntime([])
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        tool_runtime,
        HierarchicalMemory(),
        observation_engine=BlockedObservationEngine(PageIssueCode.OBSERVATION_ERROR),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Read current page"))))
    blocker_event = next(event for event in events if event.name == "page_blocker_detected")

    assert runtime.last_result is not None
    assert runtime.last_result.termination_reason is TaskTerminationReason.PAGE_BLOCKER
    assert blocker_event.details["blocker_type"] == "browser_observation_error"
    assert blocker_event.details["runtime_response"] == "restart_browser"
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


def test_runtime_replans_ungrounded_reasoning_confirmation_without_pausing():
    provider = MockLlmProvider(
        [
            _text_result("NEED_CONFIRMATION: Submit form."),
            _text_result("No concrete action was selected, so nothing was submitted."),
        ]
    )
    runtime = _runtime(
        provider,
        FakePlanningEngine(),
        FakeToolRuntime([]),
        HierarchicalMemory(),
        settings=RuntimeSettings(max_iterations=3, max_failures=2),
    )

    events = asyncio.run(_collect(runtime.run(UserTask("Submit the form"))))

    assert runtime.last_result is not None
    assert runtime.last_result.status is RuntimeStatus.COMPLETED
    assert runtime.last_result.final_state is AgentState.COMPLETED
    assert all(event.name != "confirmation_required" for event in events)
    assert any(event.name == "plan_revised" for event in events)


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


def _browser_click_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.click",
        description="Click a semantic link.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema(
                    "element_id",
                    ToolValueType.STRING,
                    "Semantic element identifier.",
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _browser_click_by_intent_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.click_by_intent",
        description="Click a link by semantic intent.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema("target", ToolValueType.STRING, "Semantic target."),
                ToolFieldSchema(
                    "role",
                    ToolValueType.STRING,
                    "Optional semantic role.",
                    required=False,
                ),
                ToolFieldSchema(
                    "context",
                    ToolValueType.STRING,
                    "Optional visible context.",
                    required=False,
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _browser_resolve_target_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.resolve_target",
        description="Resolve a semantic target without clicking.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema(
                    "kind",
                    ToolValueType.STRING,
                    "Semantic intent kind.",
                    enum_values=("click", "field", "search_field"),
                ),
                ToolFieldSchema(
                    "target",
                    ToolValueType.STRING,
                    "Visible semantic target.",
                    required=False,
                ),
                ToolFieldSchema(
                    "role",
                    ToolValueType.STRING,
                    "Optional semantic role.",
                    required=False,
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _browser_fill_by_label_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.fill_by_label",
        description="Fill a visible field by semantic label.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema(
                    "label",
                    ToolValueType.STRING,
                    "Visible field label.",
                ),
                ToolFieldSchema(
                    "value",
                    ToolValueType.STRING,
                    "Value to enter.",
                    sensitive=True,
                ),
                ToolFieldSchema(
                    "context",
                    ToolValueType.STRING,
                    "Optional visible context.",
                    required=False,
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _browser_press_key_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.press_key",
        description="Press a browser key.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema(
                    "key",
                    ToolValueType.STRING,
                    "Key name.",
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _browser_navigate_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.navigate",
        description="Navigate to an observed URL.",
        input_schema=ToolInputSchema(
            fields=(
                ToolFieldSchema(
                    "url",
                    ToolValueType.STRING,
                    "Observed URL.",
                ),
            )
        ),
        output_schema=ToolOutputSchema(),
    )


def _browser_back_schema() -> ToolSchema:
    return ToolSchema(
        name="browser.back",
        description="Return to the previous page.",
        input_schema=ToolInputSchema(),
        output_schema=ToolOutputSchema(),
    )


def _tool_call_result(name, arguments):
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(tool_calls=(LlmToolCall(name=name, arguments=arguments),)),
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
    data: dict[str, object] | None = None,
    failure_kind: ToolFailureKind | None = None,
    error_code: str | None = None,
) -> ToolExecutionResult:
    now = datetime.now(tz=timezone.utc)
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status,
        success=success,
        message=message,
        data=data or {},
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


class RepeatedLinkObservationEngine:
    def __init__(self, target_url: str):
        self.target_url = target_url
        self.count = 0

    async def observe(self):
        self.count += 1
        if self.count == 2:
            return PageObservation(
                url=self.target_url,
                title="First vacancy detail",
                summary="First vacancy requirements.",
            )
        return PageObservation(
            url="https://example.test/search",
            title="Vacancy search",
            summary="Search results.",
            interactive_elements=[
                InteractiveElement(
                    element_id="el_first",
                    role="link",
                    accessible_name="First vacancy",
                    visible_text="First vacancy",
                    target_url=self.target_url,
                )
            ],
        )


class DismissibleModalObservationEngine:
    def __init__(self):
        self.dismissed = False

    async def observe(self):
        if self.dismissed:
            return PageObservation(
                url="https://example.test/vacancies",
                title="Vacancies",
                summary="Vacancy list is visible.",
            )
        return PageObservation(
            url="https://example.test/vacancies",
            title="Vacancies",
            summary="An unrelated feedback survey is visible.",
            dialogs=[
                DialogSummary(
                    dialog_id="dialog_feedback",
                    role="dialog",
                    title="Why did you not respond?",
                    text="Optional feedback survey.",
                )
            ],
            issues=[
                PageIssue(
                    PageIssueCode.MODAL_DIALOG,
                    "A visible modal or dialog is present.",
                    severity="warning",
                )
            ],
        )


class MultipleResourceObservationEngine:
    def __init__(self, first_url: str, second_url: str):
        self.first_url = first_url
        self.second_url = second_url
        self.count = 0

    async def observe(self):
        self.count += 1
        if self.count in {1, 3}:
            return PageObservation(
                url="https://example.test/results",
                title="Results",
                summary="Two result links are visible.",
                interactive_elements=[
                    InteractiveElement(
                        element_id="el_first",
                        role="link",
                        accessible_name="First item",
                        visible_text="First item",
                        target_url=self.first_url,
                    ),
                    InteractiveElement(
                        element_id="el_second",
                        role="link",
                        accessible_name="Second item",
                        visible_text="Second item",
                        target_url=self.second_url,
                    ),
                ],
            )
        current_url = self.first_url if self.count == 2 else self.second_url
        return PageObservation(
            url=current_url,
            title="Item details",
            summary="Distinct item details are visible.",
        )


class FastMultiResourceObservationEngine:
    def __init__(self, resource_urls):
        self.resource_urls = list(resource_urls)
        self.count = 0

    async def observe(self):
        self.count += 1
        if self.count in {1, 4, 5, 8, 9}:
            return PageObservation(
                url="https://example.test/results",
                title="Results",
                summary="Distinct result links are visible.",
                interactive_elements=[
                    InteractiveElement(
                        element_id=f"el_{index}",
                        role="link",
                        accessible_name=f"Result {index}",
                        visible_text=f"Result {index}",
                        target_url=url,
                    )
                    for index, url in enumerate(self.resource_urls, start=1)
                ],
            )
        resource_index = 0 if self.count < 4 else 1 if self.count < 8 else 2
        return PageObservation(
            url=self.resource_urls[resource_index],
            title=f"Resource {resource_index + 1}",
            summary=f"Resource {resource_index + 1} details.",
            sections=[
                SemanticSection(
                    section_id=f"section_{resource_index + 1}",
                    role="main",
                    heading="Requirements",
                    text=f"Verified requirements for resource {resource_index + 1}.",
                )
            ],
        )


class ModalDismissToolRuntime:
    def __init__(self, observation_engine: DismissibleModalObservationEngine):
        self.observation_engine = observation_engine
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        if request != ToolRequest(name="browser.press_key", arguments={"key": "Escape"}):
            raise AssertionError(f"Unexpected modal dismiss request: {request}")
        self.observation_engine.dismissed = True
        return _tool_result("browser.press_key", success=True, message="Pressed Escape.")


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
