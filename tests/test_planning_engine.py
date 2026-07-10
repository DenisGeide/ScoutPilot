import asyncio
import json

from scout_pilot.llm import LlmProviderResponse, LlmProviderResult, MockLlmProvider
from scout_pilot.models import (
    ExecutionPlan,
    PageObservation,
    PlanStep,
    PlanStepStatus,
    SemanticSection,
    ToolRequest,
    UserTask,
)
from scout_pilot.planning import ProviderPlanningEngine
from scout_pilot.planning.types import PlanningSettings
from scout_pilot.tools import create_browser_tool_registry


def test_planner_creates_structured_semantic_plan():
    provider = MockLlmProvider(
        [
            _json_result(
                {
                    "summary": "Inspect the page and select the relevant result.",
                    "steps": [
                        {
                            "goal": "Observe the current page semantically.",
                            "tool_name": "browser.observe",
                            "arguments": {},
                        },
                        {
                            "goal": "Click the matching visible result by semantic ID.",
                            "tool_name": "browser.click",
                            "arguments": {"element_id": "el_result_1"},
                            "is_uncertain": True,
                            "uncertainty_reason": "The latest observation must identify the exact result.",
                        },
                    ],
                    "warnings": [],
                }
            )
        ]
    )
    engine = ProviderPlanningEngine(provider)

    plan = asyncio.run(
        engine.create_plan(
            UserTask("Find the pricing page"),
            _observation(),
            memory_summaries=["User prefers concise navigation."],
            available_tools=_schemas(),
        )
    )

    assert plan.summary == "Inspect the page and select the relevant result."
    assert [step.tool_name for step in plan.steps] == ["browser.observe", "browser.click"]
    assert plan.steps[1].arguments == {"element_id": "el_result_1"}
    assert plan.memory_summaries == ("User prefers concise navigation.",)
    assert plan.observation_url == "https://example.test"
    assert provider.requests[0].tools == ()
    assert "current_observation" in provider.requests[0].messages[1].content
    payload = json.loads(provider.requests[0].messages[1].content)
    assert payload["context_metrics"]["after_tokens"] <= payload["context_metrics"]["before_tokens"]


def test_planner_fallback_for_empty_task_does_not_call_provider():
    provider = MockLlmProvider()
    engine = ProviderPlanningEngine(provider)

    plan = asyncio.run(engine.create_plan_from_text("   ", observation=_observation()))

    assert plan.is_fallback is True
    assert plan.validation_errors
    assert plan.steps[0].is_uncertain is True
    assert provider.requests == []


def test_planner_deactivates_steps_with_selectors():
    provider = MockLlmProvider(
        [
            _json_result(
                {
                    "summary": "Click the login button.",
                    "steps": [
                        {
                            "goal": "Click #login.",
                            "tool_name": "browser.click",
                            "arguments": {"element_id": "#login"},
                        }
                    ],
                    "warnings": [],
                }
            )
        ]
    )
    engine = ProviderPlanningEngine(provider)

    plan = asyncio.run(
        engine.create_plan(
            UserTask("Open the login form"),
            _observation(),
            available_tools=_schemas(),
        )
    )

    assert plan.validation_errors
    assert "CSS selectors" in plan.validation_errors[0]
    assert plan.steps[0].tool_name is None
    assert plan.steps[0].is_uncertain is True


def test_planner_marks_side_effect_steps_for_confirmation():
    provider = MockLlmProvider(
        [
            _json_result(
                {
                    "summary": "Prepare to submit the form.",
                    "steps": [
                        {
                            "goal": "Review the form contents.",
                            "tool_name": "browser.observe",
                            "arguments": {},
                        },
                        {
                            "goal": "Submit the completed application.",
                            "tool_name": "browser.click",
                            "arguments": {"element_id": "submit_button"},
                            "requires_confirmation": False,
                        },
                    ],
                    "warnings": [],
                }
            )
        ]
    )
    engine = ProviderPlanningEngine(provider)

    plan = asyncio.run(
        engine.create_plan(
            UserTask("Submit the application"),
            _observation(),
            available_tools=_schemas(),
        )
    )

    assert plan.steps[0].requires_confirmation is False
    assert plan.steps[1].requires_confirmation is True


def test_replanning_preserves_completed_steps():
    completed_step = PlanStep(
        step_id="done_1",
        goal="Observe the starting page.",
        status=PlanStepStatus.COMPLETED,
        tool_request=ToolRequest(name="browser.observe", arguments={}),
    )
    original = ExecutionPlan(
        task=UserTask("Find a relevant page"),
        steps=[
            completed_step,
            PlanStep(step_id="pending_1", goal="Choose a result.", tool_name="browser.click"),
        ],
        summary="Original plan.",
    )
    provider = MockLlmProvider(
        [
            _json_result(
                {
                    "summary": "Use the updated observation.",
                    "steps": [
                        {
                            "goal": "Click the relevant visible result by semantic ID.",
                            "tool_name": "browser.click",
                            "arguments": {"element_id": "el_result_2"},
                        }
                    ],
                    "warnings": [],
                }
            )
        ]
    )
    engine = ProviderPlanningEngine(provider)

    revised = asyncio.run(
        engine.revise_plan(
            original,
            _observation(),
            reason="The page changed after navigation.",
            available_tools=_schemas(),
        )
    )

    assert revised.steps[0] == completed_step
    assert revised.steps[1].goal == "Click the relevant visible result by semantic ID."
    assert revised.revision_reason == "The page changed after navigation."


def test_malformed_provider_response_returns_fallback_plan():
    provider = MockLlmProvider(
        [LlmProviderResult(success=True, response=LlmProviderResponse(content="not json"))]
    )
    engine = ProviderPlanningEngine(provider)

    plan = asyncio.run(
        engine.create_plan(
            UserTask("Find the help page"),
            _observation(),
            available_tools=_schemas(),
        )
    )

    assert plan.is_fallback is True
    assert plan.validation_errors
    assert "could not be parsed" in plan.validation_errors[0]


def test_planner_request_uses_budgeted_context_for_oversized_inputs():
    provider = MockLlmProvider(
        [
            _json_result(
                {
                    "summary": "Use the compact observation.",
                    "steps": [{"goal": "Observe the page.", "tool_name": "browser.observe"}],
                    "warnings": [],
                }
            )
        ]
    )
    engine = ProviderPlanningEngine(
        provider,
        settings=PlanningSettings(
            max_input_tokens=650,
            max_output_tokens=200,
            max_prompt_observation_tokens=180,
            max_memory_tokens=90,
            max_memory_summaries=4,
        ),
    )
    memory = [
        *(f"working.observation: repeated navigation snapshot {index}" for index in range(12)),
        "task.constraint: do not submit forms.",
        "security warning: never expose cookies.",
    ]

    plan = asyncio.run(
        engine.create_plan(
            UserTask("Find the relevant result"),
            _large_observation(),
            memory_summaries=memory,
            available_tools=_schemas(),
        )
    )

    payload = json.loads(provider.requests[0].messages[1].content)
    metrics = payload["context_metrics"]
    assert plan.memory_summaries == tuple(payload["memory_summaries"])
    assert metrics["after_tokens"] < metrics["before_tokens"]
    assert metrics["dropped_sections"] > 0
    assert "task.constraint" in " ".join(payload["memory_summaries"])
    assert "<html" not in provider.requests[0].messages[1].content.casefold()


def _json_result(payload: dict):
    return LlmProviderResult(
        success=True,
        response=LlmProviderResponse(content=json.dumps(payload)),
    )


def _observation() -> PageObservation:
    return PageObservation(
        url="https://example.test",
        title="Example",
        summary="A compact page with navigation and search results.",
    )


def _large_observation() -> PageObservation:
    repeated = "Navigation Home Jobs Profile Messages " * 20
    return PageObservation(
        url="https://example.test/search",
        title="Large",
        summary="Large search results page." * 40,
        sections=[
            *(SemanticSection(f"nav_{index}", "navigation", "Menu", repeated) for index in range(8)),
            *(
                SemanticSection(
                    f"result_{index}",
                    "main",
                    f"Result {index}",
                    (
                        "Visible result with useful task-relevant text and details. "
                        "This should be prioritized over repeated navigation. "
                    )
                    * 20,
                )
                for index in range(10)
            ),
        ],
    )


def _schemas():
    return create_browser_tool_registry().schemas()
