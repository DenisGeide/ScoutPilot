from scout_pilot.models import ContextBudget, PageObservation, SemanticElement, UserTask


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


def test_context_budget_remaining_tokens_never_negative():
    budget = ContextBudget(max_tokens=100, reserved_tokens=60, used_tokens=80)

    assert budget.remaining_tokens == 0
