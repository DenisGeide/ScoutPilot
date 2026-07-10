from scout_pilot.context import ContextBudgetSettings, DeterministicContextBudgeter
from scout_pilot.models import (
    ContextBudget,
    DialogSummary,
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


def test_oversized_local_page_records_kept_and_dropped_context_evidence():
    observation = _large_observation_with_dialog_and_forms()
    memory = [
        "task.user_goal: compare three AI Engineer vacancies.",
        "task.constraint: stop before applying.",
        "security warning: applying requires explicit confirmation.",
    ]
    budgeter = DeterministicContextBudgeter(
        ContextBudgetSettings(
            max_input_tokens=900,
            reserved_output_tokens=200,
            max_observation_tokens=420,
            max_memory_tokens=160,
            max_section_chars=140,
            max_summary_chars=160,
        )
    )

    budgeted = budgeter.assemble(
        user_task="Find three suitable AI Engineer vacancies and stop before applying.",
        observation=observation,
        memory_summaries=memory,
        max_input_tokens=900,
        reserved_output_tokens=200,
    )
    metrics = budgeted.metrics.to_dict()

    assert metrics["before_tokens"] > metrics["after_tokens"]
    assert metrics["observation_sections_before"] == len(observation.sections)
    assert metrics["observation_sections_kept"] == len(budgeted.observation.sections)
    assert 0 < metrics["observation_sections_kept"] < metrics["observation_sections_before"]
    assert metrics["observation_sections_dropped"] > 0
    assert metrics["dialogs_kept"] >= 1
    assert metrics["form_fields_kept"] >= 1
    assert metrics["memory_summaries_kept"] == len(budgeted.memory_summaries)
    assert "<html" not in str(budgeted.observation.to_llm_context()).casefold()


def test_long_task_history_metrics_show_memory_kept_and_dropped():
    summaries = [
        *(f"working.observation: stale repeated page chrome {index}" for index in range(30)),
        "task.user_goal: find relevant Python AI Developer roles.",
        "task.constraint: only prepare notes, do not send applications.",
        "task.confirmed_choice: user wants remote-friendly vacancies.",
        "security warning: external side effects require confirmation.",
        "failure: previous result click opened an irrelevant page.",
    ]
    budgeter = DeterministicContextBudgeter(
        ContextBudgetSettings(
            max_input_tokens=760,
            reserved_output_tokens=220,
            max_observation_tokens=180,
            max_memory_tokens=90,
            max_memory_summaries=5,
        )
    )

    budgeted = budgeter.assemble(
        user_task="Prepare vacancy notes.",
        observation=PageObservation(
            url="https://example.test",
            title="Compact",
            summary="Short page summary.",
        ),
        memory_summaries=summaries,
        max_input_tokens=760,
        reserved_output_tokens=220,
    )
    metrics = budgeted.metrics.to_dict()
    joined = " ".join(budgeted.memory_summaries).casefold()

    assert metrics["memory_summaries_before"] == len(summaries)
    assert metrics["memory_summaries_kept"] == len(budgeted.memory_summaries)
    assert metrics["memory_summaries_dropped"] > 0
    assert metrics["preserved_critical_facts"] >= 3
    assert "user_goal" in joined
    assert "constraint" in joined
    assert "security warning" in joined
    assert "stale repeated page chrome 0" not in joined


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


def _large_observation_with_dialog_and_forms() -> PageObservation:
    base = _large_observation()
    return PageObservation(
        url=base.url,
        title=base.title,
        summary=base.summary,
        sections=base.sections,
        interactive_elements=base.interactive_elements,
        form_fields=base.form_fields,
        dialogs=[
            DialogSummary(
                "dialog_1",
                "dialog",
                "Confirmation",
                "Visible dialog asks whether the user wants to apply now.",
            )
        ],
    )
