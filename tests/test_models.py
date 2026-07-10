from scout_pilot.models import (
    ContextBudget,
    ElementState,
    FormFieldSummary,
    PageObservation,
    SemanticElement,
    UserTask,
)


def test_user_task_rejects_empty_text():
    try:
        UserTask("   ")
    except ValueError as exc:
        assert "cannot be empty" in str(exc)
    else:
        raise AssertionError("Expected ValueError for empty task")


def test_page_observation_context_excludes_raw_html_keys():
    observation = PageObservation(
        url="https://example.test",
        title="Example",
        summary="A compact page summary.",
        elements=[SemanticElement(role="button", label="Search", is_interactive=True)],
    )

    context = observation.to_llm_context()

    assert "html" not in context
    assert "dom" not in context
    assert context["elements"][0]["label"] == "Search"


def test_page_observation_form_context_does_not_expose_values():
    observation = PageObservation(
        url="https://example.test",
        title="Example",
        summary="A compact page summary.",
        form_fields=[
            FormFieldSummary(
                field_id="field_123",
                role="textbox",
                input_type="password",
                label="Password",
                placeholder="Password",
                value_state="redacted_filled",
                state=ElementState(required=True),
            )
        ],
    )

    context_text = str(observation.to_llm_context())

    assert "redacted_filled" in context_text
    assert "secret" not in context_text.lower()


def test_context_budget_remaining_tokens_never_negative():
    budget = ContextBudget(max_tokens=100, reserved_tokens=60, used_tokens=80)

    assert budget.remaining_tokens == 0
