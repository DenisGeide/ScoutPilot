from scout_pilot.context import ContextBudgetSettings, DeterministicContextBudgeter
from scout_pilot.models import (
    ContextBudget,
    FormFieldSummary,
    InteractiveElement,
    PageIssueCode,
    PageObservation,
    SemanticSection,
)


def test_oversized_observation_is_compressed_with_metrics():
    observation = _large_observation()
    budgeter = DeterministicContextBudgeter(
        ContextBudgetSettings(
            max_section_chars=160,
            max_summary_chars=180,
        )
    )

    fitted = budgeter.fit_observation(
        observation,
        ContextBudget(max_tokens=700, reserved_tokens=0, used_tokens=0),
    )

    assert budgeter.last_metrics is not None
    assert budgeter.last_metrics.observation_after_tokens < (
        budgeter.last_metrics.observation_before_tokens
    )
    assert budgeter.last_metrics.observation_after_tokens <= 700
    assert len(fitted.sections) < len(observation.sections)
    assert any(issue.code is PageIssueCode.OBSERVATION_TRUNCATED for issue in fitted.issues)


def test_repeated_navigation_header_footer_content_is_deduplicated():
    repeated = "Repeated navigation Home Jobs Messages Settings"
    observation = PageObservation(
        url="https://example.test",
        title="Repeated",
        summary="Page with repeated boilerplate.",
        sections=[
            SemanticSection(f"nav_{index}", "navigation", "Menu", repeated)
            for index in range(5)
        ]
        + [
            SemanticSection("main", "main", "Results", "Visible search result content."),
            SemanticSection("footer", "footer", "Footer", repeated),
        ],
    )
    budgeter = DeterministicContextBudgeter()

    fitted = budgeter.fit_observation(
        observation,
        ContextBudget(max_tokens=2000, reserved_tokens=0, used_tokens=0),
    )

    repeated_sections = [section for section in fitted.sections if section.text == repeated]
    assert len(repeated_sections) == 1
    assert budgeter.last_metrics is not None
    assert budgeter.last_metrics.deduplicated_items >= 4


def test_long_memory_history_preserves_critical_facts_and_drops_stale_items():
    summaries = [
        *(f"working.observation: stale header/footer snapshot {index}" for index in range(20)),
        "task.user_goal: find a remote Python role.",
        "task.constraint: do not submit applications.",
        "task.confirmed_choice: user selected English vacancies.",
        "security warning: do not expose tokens or cookies.",
        "failure: previous click did not change the page.",
    ]
    budgeter = DeterministicContextBudgeter(
        ContextBudgetSettings(max_memory_tokens=90, max_memory_summaries=5)
    )

    fitted = budgeter.fit_memory_summaries(summaries, max_tokens=90, max_items=5)
    joined = " ".join(fitted).casefold()

    assert "user_goal" in joined
    assert "constraint" in joined
    assert "confirmed_choice" in joined
    assert "security warning" in joined
    assert len(fitted) <= 5
    assert "stale header/footer snapshot 0" not in joined


def test_emergency_compression_reports_before_after_metrics():
    budgeter = DeterministicContextBudgeter(
        ContextBudgetSettings(
            max_input_tokens=420,
            reserved_output_tokens=220,
            max_observation_tokens=500,
            max_memory_tokens=250,
            emergency_observation_tokens=120,
            emergency_memory_tokens=60,
            max_section_chars=120,
            max_summary_chars=160,
        )
    )

    budgeted = budgeter.assemble(
        user_task="Find the relevant result without submitting anything.",
        observation=_large_observation(),
        memory_summaries=[
            f"working.observation: repeated low-value snapshot {index}"
            for index in range(30)
        ],
        max_input_tokens=420,
        reserved_output_tokens=220,
    )

    assert budgeted.metrics.emergency_compression_applied is True
    assert budgeted.metrics.after_tokens < budgeted.metrics.before_tokens
    assert budgeted.budget["estimated_input_tokens_after"] == budgeted.metrics.after_tokens
    assert budgeted.budget["remaining_tokens"] >= 0


def test_raw_markup_like_sections_are_not_preserved():
    observation = PageObservation(
        url="https://example.test",
        title="Markup",
        summary="Synthetic page.",
        sections=[
            SemanticSection(
                "raw",
                "main",
                "Raw",
                "<html><body><div>Secret raw markup</div></body></html>",
            )
        ],
    )
    budgeter = DeterministicContextBudgeter()

    fitted = budgeter.fit_observation(
        observation,
        ContextBudget(max_tokens=1000, reserved_tokens=0, used_tokens=0),
    )

    assert fitted.sections == ()
    assert "Secret raw markup" not in str(fitted.to_llm_context())


def _large_observation() -> PageObservation:
    repeated = "Header Home Jobs Messages Profile " * 20
    sections = [
        SemanticSection(f"header_{index}", "header", "Header", repeated)
        for index in range(8)
    ]
    sections.extend(
        SemanticSection(
            f"result_{index}",
            "main",
            f"Result {index}",
            (
                f"Relevant visible search result {index}. "
                "Python automation browser agent remote role with detailed description. "
            )
            * 25,
        )
        for index in range(12)
    )
    sections.extend(
        SemanticSection(f"footer_{index}", "footer", "Footer", repeated)
        for index in range(6)
    )
    return PageObservation(
        url="https://example.test/search",
        title="Search",
        summary="Search results page with visible results and repeated navigation." * 20,
        sections=sections,
        interactive_elements=[
            InteractiveElement(
                element_id=f"el_{index}",
                role="link",
                accessible_name=f"Open result {index}",
                visible_text=f"Open result {index}",
            )
            for index in range(20)
        ],
        form_fields=[
            FormFieldSummary(
                field_id=f"field_{index}",
                role="textbox",
                input_type="text",
                label=f"Filter {index}",
                placeholder="Type filter",
                value_state="filled",
            )
            for index in range(10)
        ],
    )
