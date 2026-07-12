import pytest

from scout_pilot.models import (
    ActionRisk,
    FocusedElementSummary,
    FormFieldSummary,
    InteractiveElement,
    PageObservation,
    ToolRequest,
)
from scout_pilot.security import DeterministicSecurityPolicy, SecurityEvaluationContext


def test_policy_allows_safe_navigation_and_records_audit():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(
            name="browser.navigate",
            arguments={"url": "https://example.test"},
        )
    )

    assert decision.allowed is True
    assert decision.risk is ActionRisk.SAFE
    assert decision.requires_confirmation is False
    assert policy.audit_trail[-1].outcome == "allowed"


def test_policy_blocks_local_file_navigation():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(
            name="browser.navigate",
            arguments={"url": "file:///C:/Users/Unknown/.env"},
        )
    )

    assert decision.allowed is False
    assert decision.blocked is True
    assert decision.requires_confirmation is False
    assert policy.audit_trail[-1].outcome == "blocked"


def test_policy_requires_confirmation_for_sensitive_fill():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(
            name="browser.fill",
            arguments={"element_id": "field_email", "value": "private@example.test"},
        ),
        SecurityEvaluationContext(sensitive_fields=frozenset({"value"})),
    )

    assert decision.allowed is False
    assert decision.risk is ActionRisk.SENSITIVE
    assert decision.requires_confirmation is True
    assert decision.confirmation is not None
    assert "Требуется подтверждение" in decision.confirmation.message_ru
    assert "Действие:" in decision.confirmation.message_ru
    assert "Почему нужна пауза:" in decision.confirmation.message_ru
    assert "Если подтвердить" in decision.confirmation.message_ru
    assert "Чтобы отменить" in decision.confirmation.message_ru
    assert decision.confirmation.redacted_arguments["value"] == "[REDACTED]"


@pytest.mark.parametrize("tool_name", ["browser.fill", "browser.fill_by_label"])
def test_policy_allows_filling_a_semantic_search_field(tool_name):
    policy = DeterministicSecurityPolicy()
    arguments = (
        {"element_id": "field_search", "value": "AI Engineer"}
        if tool_name == "browser.fill"
        else {"label": "Search vacancies", "value": "AI Engineer"}
    )

    decision = policy.evaluate(
        ToolRequest(name=tool_name, arguments=arguments),
        SecurityEvaluationContext(
            observation=_search_observation(),
            sensitive_fields=frozenset({"value"}),
        ),
    )

    assert decision.allowed is True
    assert decision.risk is ActionRisk.SAFE
    assert decision.requires_confirmation is False
    assert decision.classification.matched_terms == ("search_field",)


def test_policy_allows_enter_when_search_field_is_focused():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(name="browser.press_key", arguments={"key": "Enter"}),
        SecurityEvaluationContext(observation=_search_observation(focused=True)),
    )

    assert decision.allowed is True
    assert decision.risk is ActionRisk.SAFE
    assert decision.requires_confirmation is False


def test_policy_still_pauses_enter_in_an_unknown_form():
    policy = DeterministicSecurityPolicy()
    observation = PageObservation(
        url="https://example.test/contact",
        title="Contact",
        summary="Contact form.",
        focused_element=FocusedElementSummary(
            role="textbox",
            accessible_name="Message",
            visible_text=None,
            input_type="text",
        ),
    )

    decision = policy.evaluate(
        ToolRequest(name="browser.press_key", arguments={"key": "Enter"}),
        SecurityEvaluationContext(observation=observation),
    )

    assert decision.allowed is False
    assert decision.risk is ActionRisk.EXTERNAL_SIDE_EFFECT
    assert decision.requires_confirmation is True


def test_policy_requires_confirmation_for_destructive_click():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(name="browser.click", arguments={"element_id": "el_delete"}),
        SecurityEvaluationContext(observation=_observation("el_delete", "Delete account")),
    )

    assert decision.allowed is False
    assert decision.risk is ActionRisk.DESTRUCTIVE
    assert decision.requires_confirmation is True
    assert "удалены" in decision.confirmation.expected_consequence


def test_policy_requires_confirmation_for_external_side_effect_click():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(name="browser.click", arguments={"element_id": "el_apply"}),
        SecurityEvaluationContext(observation=_observation("el_apply", "Apply to vacancy")),
    )

    assert decision.allowed is False
    assert decision.risk is ActionRisk.EXTERNAL_SIDE_EFFECT
    assert decision.requires_confirmation is True
    assert "отклик" in decision.classification.expected_consequence


@pytest.mark.parametrize(
    ("label", "risk"),
    [
        ("Apply to vacancy", ActionRisk.EXTERNAL_SIDE_EFFECT),
        ("Send message", ActionRisk.EXTERNAL_SIDE_EFFECT),
        ("Delete account", ActionRisk.DESTRUCTIVE),
        ("Purchase now", ActionRisk.EXTERNAL_SIDE_EFFECT),
        ("Checkout", ActionRisk.EXTERNAL_SIDE_EFFECT),
    ],
)
def test_policy_pauses_external_or_destructive_clicks(label, risk):
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(name="browser.click", arguments={"element_id": "el_action"}),
        SecurityEvaluationContext(observation=_observation("el_action", label)),
    )

    assert decision.allowed is False
    assert decision.risk is risk
    assert decision.requires_confirmation is True
    assert decision.confirmation is not None


def test_policy_ignores_llm_supplied_safe_risk():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(
            name="browser.click",
            arguments={"element_id": "el_send"},
            risk=ActionRisk.SAFE,
        ),
        SecurityEvaluationContext(observation=_observation("el_send", "Send message")),
    )

    assert decision.allowed is False
    assert decision.risk is ActionRisk.EXTERNAL_SIDE_EFFECT
    assert decision.requires_confirmation is True


def test_policy_resolves_click_intent_before_classification():
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(
            name="browser.click_by_intent",
            arguments={"target": "search", "role": "button"},
        ),
        SecurityEvaluationContext(
            observation=PageObservation(
                url="https://example.test",
                title="Search",
                summary="Search fixture.",
                interactive_elements=[
                    InteractiveElement(
                        element_id="el_search",
                        role="button",
                        accessible_name="Search",
                        visible_text="Search",
                        input_type="submit",
                    )
                ],
            )
        ),
    )

    assert decision.allowed is True
    assert decision.risk is ActionRisk.SAFE
    assert decision.requires_confirmation is False
    assert "search" in decision.classification.matched_terms


@pytest.mark.parametrize(
    "label",
    ["Search", "Submit search", "Apply filters", "Показать вакансии"],
)
def test_policy_allows_read_only_search_and_filter_clicks(label):
    policy = DeterministicSecurityPolicy()

    decision = policy.evaluate(
        ToolRequest(name="browser.click", arguments={"element_id": "el_search"}),
        SecurityEvaluationContext(
            observation=PageObservation(
                url="https://example.test/jobs",
                title="Jobs",
                summary="Search results.",
                interactive_elements=[
                    InteractiveElement(
                        element_id="el_search",
                        role="button",
                        accessible_name=label,
                        visible_text=label,
                        input_type="submit",
                    )
                ],
            )
        ),
    )

    assert decision.allowed is True
    assert decision.risk is ActionRisk.SAFE
    assert decision.requires_confirmation is False


def test_confirmed_exact_request_is_allowed_once_by_policy_context():
    policy = DeterministicSecurityPolicy()
    request = ToolRequest(name="browser.click", arguments={"element_id": "el_submit"})
    context = SecurityEvaluationContext(observation=_observation("el_submit", "Submit form"))

    first = policy.evaluate(request, context)
    second = policy.evaluate(
        request,
        SecurityEvaluationContext(
            observation=context.observation,
            is_confirmed=True,
        ),
    )

    assert first.requires_confirmation is True
    assert second.allowed is True
    assert second.requires_confirmation is False
    assert policy.audit_trail[-1].outcome == "allowed"


def _observation(element_id: str, label: str) -> PageObservation:
    return PageObservation(
        url="https://example.test",
        title="Security",
        summary="Security fixture.",
        interactive_elements=[
            InteractiveElement(
                element_id=element_id,
                role="button",
                accessible_name=label,
                visible_text=label,
            )
        ],
    )


def _search_observation(*, focused: bool = False) -> PageObservation:
    return PageObservation(
        url="https://example.test/jobs",
        title="Vacancy search",
        summary="Search vacancies.",
        form_fields=[
            FormFieldSummary(
                field_id="field_search",
                role="searchbox",
                input_type="search",
                label="Search vacancies",
                placeholder="Job title or company",
                value_state="empty",
            )
        ],
        focused_element=(
            FocusedElementSummary(
                role="searchbox",
                accessible_name="Search vacancies",
                visible_text=None,
                input_type="search",
            )
            if focused
            else None
        ),
    )
