import asyncio
import json

from scout_pilot.llm import LlmProviderResponse, LlmProviderResult, MockLlmProvider
from scout_pilot.models import (
    ExecutionPlan,
    PageObservation,
    PlanStep,
    PlanStepStatus,
    ToolRequest,
    UserTask,
)
from scout_pilot.planning import ProviderPlanningEngine
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


def _schemas():
    return create_browser_tool_registry().schemas()
