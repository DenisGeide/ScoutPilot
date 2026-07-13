import asyncio
from pathlib import Path

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.browser.types import BrowserActionResult
from scout_pilot.demo import LocalDemoServer
from scout_pilot.models import (
    ElementLocation,
    ElementState,
    FormFieldSummary,
    InteractiveElement,
    PageMetadata,
    PageObservation,
    SemanticSection,
    ToolRequest,
)
from scout_pilot.navigation import (
    NavigationIntent,
    NavigationIntentKind,
    SemanticNavigationResolver,
    SemanticResolutionStatus,
)
from scout_pilot.tools import (
    DefaultToolRuntime,
    ToolContext,
    ToolExecutionStatus,
    create_browser_tool_registry,
)
from scout_pilot.tools.browser_tools import ClickByIntentTool
from scout_pilot.tools.browser_tools import ClickTool


def test_same_search_intent_works_on_different_local_markup(tmp_path):
    site_a = _write_site_a(tmp_path)
    site_b = _write_site_b(tmp_path)

    first_title = _run_semantic_search_flow(tmp_path / "a-profile", site_a)
    second_title = _run_semantic_search_flow(tmp_path / "b-profile", site_b)

    assert first_title == "Alpha A"
    assert second_title == "Alpha B"


def test_resolver_reports_ambiguous_click_targets_without_guessing():
    observation = PageObservation(
        url="https://example.test",
        title="Ambiguous",
        summary="Two equivalent actions.",
        interactive_elements=[
            InteractiveElement(
                element_id="el_first",
                role="button",
                accessible_name="Open",
                visible_text="Open",
            ),
            InteractiveElement(
                element_id="el_second",
                role="button",
                accessible_name="Open",
                visible_text="Open",
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve_click(
        observation,
        target="Open",
        role="button",
    )

    assert resolution.status is SemanticResolutionStatus.AMBIGUOUS
    assert resolution.selected is None
    assert [candidate.element_id for candidate in resolution.candidates] == [
        "el_first",
        "el_second",
    ]


def test_resolver_coalesces_duplicate_links_to_the_same_destination():
    target_url = "https://example.test/items/1001?source=results"
    observation = PageObservation(
        url="https://example.test/results",
        title="Results",
        summary="The same destination is rendered twice.",
        interactive_elements=[
            InteractiveElement(
                element_id="el_title",
                role="link",
                accessible_name="Lead AI Engineer",
                visible_text="Lead AI Engineer",
                target_url=target_url,
            ),
            InteractiveElement(
                element_id="el_card",
                role="link",
                accessible_name="Lead AI Engineer",
                visible_text="Lead AI Engineer",
                target_url="https://example.test/items/1001?from=card#description",
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve_click(
        observation,
        target="Lead AI Engineer",
        role="link",
    )

    assert resolution.status is SemanticResolutionStatus.RESOLVED
    assert resolution.selected is not None
    assert resolution.selected.element_id == "el_title"


def test_resolver_prefers_named_resource_link_over_collection_view():
    location = ElementLocation("middle", x_ratio=0.4, y_ratio=0.5)
    observation = PageObservation(
        url="https://example.test/search",
        title="Results",
        summary="A result title is also rendered in a collection view.",
        sections=[
            SemanticSection(
                "sec_results",
                "section",
                "Results",
                "50 000 - 100 000 per month, Example Company",
                location=location,
            )
        ],
        interactive_elements=[
            InteractiveElement(
                element_id="el_collection",
                role="link",
                accessible_name="Data scientist",
                visible_text="Data scientist",
                target_url="https://example.test/search/map?item=data-scientist",
                location=location,
            ),
            InteractiveElement(
                element_id="el_detail",
                role="link",
                accessible_name="Data scientist",
                visible_text="Data scientist",
                target_url="https://example.test/items/135124449?source=search",
                location=location,
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve_click(
        observation,
        target="Data scientist",
        role="link",
        context="50 000 100 000 Example Company",
    )

    assert resolution.status is SemanticResolutionStatus.RESOLVED
    assert resolution.selected is not None
    assert resolution.selected.element_id == "el_detail"
    assert "stable_resource_link" in resolution.selected.reasons


def test_resolver_selects_contextual_read_only_link_when_titles_are_equal():
    location = ElementLocation("middle", x_ratio=0.4, y_ratio=0.5)
    observation = PageObservation(
        url="https://example.test/results",
        title="Results",
        summary="Two read-only links share a title.",
        sections=[
            SemanticSection(
                "sec_results",
                "section",
                "Vacancies",
                "270 000 per month, Example Rail Company",
                location=location,
            )
        ],
        interactive_elements=[
            InteractiveElement(
                element_id="el_first",
                role="link",
                accessible_name="Artificial Intelligence Engineer",
                visible_text="Artificial Intelligence Engineer",
                target_url="https://example.test/items/1001",
                location=location,
            ),
            InteractiveElement(
                element_id="el_second",
                role="link",
                accessible_name="Artificial Intelligence Engineer",
                visible_text="Artificial Intelligence Engineer",
                target_url="https://example.test/items/1002",
                location=location,
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve_click(
        observation,
        target="Artificial Intelligence Engineer",
        role="link",
        context="270 000 Example Rail Company",
    )

    assert resolution.status is SemanticResolutionStatus.RESOLVED
    assert resolution.selected is not None
    assert resolution.selected.element_id == "el_first"
    assert resolution.message.startswith("Contextual read-only")


def test_resolver_uses_section_context_and_location_to_disambiguate_buttons():
    top = ElementLocation("top", x_ratio=0.2, y_ratio=0.2)
    bottom = ElementLocation("bottom", x_ratio=0.2, y_ratio=0.8)
    observation = PageObservation(
        url="https://example.test",
        title="Results",
        summary="Two cards share the same visible action.",
        sections=[
            SemanticSection(
                "sec_alpha",
                "article",
                "Alpha role",
                "Alpha Engineer role with Python requirements.",
                location=top,
            ),
            SemanticSection(
                "sec_beta",
                "article",
                "Beta role",
                "Beta AI Engineer role with LLM evaluation requirements.",
                location=bottom,
            ),
        ],
        interactive_elements=[
            InteractiveElement(
                element_id="el_alpha_details",
                role="button",
                accessible_name="Details",
                visible_text="Details",
                location=top,
            ),
            InteractiveElement(
                element_id="el_beta_details",
                role="button",
                accessible_name="Details",
                visible_text="Details",
                location=bottom,
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve_click(
        observation,
        target="Details",
        role="button",
        context="Beta LLM evaluation",
    )

    assert resolution.status is SemanticResolutionStatus.RESOLVED
    assert resolution.selected is not None
    assert resolution.selected.element_id == "el_beta_details"
    assert "context_terms_match" in resolution.selected.reasons
    assert resolution.selected.location_bucket is not None


def test_resolver_ignores_disabled_candidate_before_matching():
    observation = PageObservation(
        url="https://example.test",
        title="Disabled",
        summary="A disabled action and an enabled action share a name.",
        interactive_elements=[
            InteractiveElement(
                element_id="el_disabled",
                role="button",
                accessible_name="Apply",
                visible_text="Apply",
                state=ElementState(disabled=True),
            ),
            InteractiveElement(
                element_id="el_enabled",
                role="button",
                accessible_name="Apply",
                visible_text="Apply",
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve_click(
        observation,
        target="Apply",
        role="button",
    )

    assert resolution.status is SemanticResolutionStatus.RESOLVED
    assert resolution.selected is not None
    assert resolution.selected.element_id == "el_enabled"


def test_stale_element_recovery_reobserves_and_remaps_candidate():
    browser = StaleFakeBrowser()
    observer = StaleObservationEngine()

    result = asyncio.run(
        ClickByIntentTool().execute(
            {"target": "Continue", "role": "button", "context": None},
            ToolContext(browser=browser, observation_engine=observer),
        )
    )

    assert result.success is True
    assert result.data["recovered_from_stale"] is True
    assert browser.clicked_ids == ["el_old", "el_new"]
    assert observer.calls == 3


def test_click_by_current_observation_id_recovers_stale_result_card():
    browser = StaleFakeBrowser()
    observer = StaleObservationEngine()

    result = asyncio.run(
        ClickTool().execute(
            {"element_id": "el_old"},
            ToolContext(browser=browser, observation_engine=observer),
        )
    )

    assert result.success is True
    assert result.data["recovered_from_stale"] is True
    assert result.data["resolution"]["selected"]["id"] == "el_new"
    assert result.data["transition"]["changed"] is True
    assert browser.clicked_ids == ["el_old", "el_new"]
    assert observer.calls == 3


def test_stale_remap_reports_ambiguity_instead_of_guessing():
    before = PageObservation(
        url="https://example.test",
        title="Before",
        summary="One old result.",
        interactive_elements=[
            InteractiveElement("el_old", "button", "Open", "Open"),
        ],
    )
    after = PageObservation(
        url="https://example.test",
        title="After",
        summary="Two replacement results.",
        interactive_elements=[
            InteractiveElement("el_new_a", "button", "Open", "Open"),
            InteractiveElement("el_new_b", "button", "Open", "Open"),
        ],
    )

    resolution = SemanticNavigationResolver().remap_click_candidate(
        before,
        after,
        "el_old",
    )

    assert resolution.status is SemanticResolutionStatus.AMBIGUOUS
    assert resolution.selected is None
    assert {candidate.element_id for candidate in resolution.candidates} == {
        "el_new_a",
        "el_new_b",
    }


def test_search_field_detection_uses_generic_semantics():
    observation = PageObservation(
        url="https://example.test",
        title="Search",
        summary="Generic form.",
        form_fields=[
            FormFieldSummary(
                field_id="field_name",
                role="textbox",
                input_type="text",
                label="Name",
                placeholder=None,
                value_state="empty",
            ),
            FormFieldSummary(
                field_id="field_query",
                role="textbox",
                input_type="search",
                label=None,
                placeholder="Find records",
                value_state="empty",
            ),
        ],
    )

    resolution = SemanticNavigationResolver().resolve(
        observation,
        NavigationIntent(NavigationIntentKind.SEARCH_FIELD),
    )

    assert resolution.status is SemanticResolutionStatus.RESOLVED
    assert resolution.selected is not None
    assert resolution.selected.element_id == "field_query"


def test_form_fill_plan_maps_semantic_labels_to_field_ids():
    observation = PageObservation(
        url="https://example.test",
        title="Form",
        summary="Generic form.",
        form_fields=[
            FormFieldSummary(
                field_id="field_email",
                role="textbox",
                input_type="email",
                label="Email address",
                placeholder=None,
                value_state="empty",
            ),
            FormFieldSummary(
                field_id="field_message",
                role="textbox",
                input_type="textarea",
                label="Message",
                placeholder="Short note",
                value_state="empty",
            ),
        ],
    )

    plan = SemanticNavigationResolver().plan_form_fill(
        observation,
        ["email address", "message"],
    )

    assert plan.is_complete is True
    assert [step.field_id for step in plan.steps] == ["field_email", "field_message"]
    assert "private" not in str(plan.to_dict()).casefold()


def test_transition_detection_covers_load_state_main_content_and_repeats():
    resolver = SemanticNavigationResolver()
    before = PageObservation(
        url="https://example.test",
        title="Results",
        summary="Results page.",
        metadata=PageMetadata("https://example.test", "Results", "https://example.test", "loading", True),
        sections=[
            SemanticSection("main", "main", "Results", "Old visible result."),
        ],
    )
    loaded = PageObservation(
        url="https://example.test",
        title="Results",
        summary="Results page.",
        metadata=PageMetadata("https://example.test", "Results", "https://example.test", "networkidle", True),
        sections=[
            SemanticSection("main", "main", "Results", "Old visible result."),
        ],
    )
    changed = PageObservation(
        url="https://example.test",
        title="Results",
        summary="Results page.",
        metadata=PageMetadata("https://example.test", "Results", "https://example.test", "networkidle", True),
        sections=[
            SemanticSection("main", "main", "Results", "New visible result."),
        ],
    )

    load_transition = resolver.detect_transition(before, loaded)
    content_transition = resolver.detect_transition(loaded, changed)
    repeated_transition = resolver.detect_transition(changed, changed)

    assert load_transition.changed is True
    assert load_transition.reason == "load_state_changed"
    assert content_transition.changed is True
    assert content_transition.reason == "main_content_changed"
    assert repeated_transition.changed is False
    assert repeated_transition.reason == "repeated_observation"
    assert repeated_transition.repeated is True


def test_dynamic_popup_is_detected_through_semantic_transition(tmp_path):
    page = _write_popup_site(tmp_path)
    title = _run_dynamic_popup_flow(tmp_path / "popup-profile", page)

    assert title == "Popup complete"


def test_navigation_source_has_no_demo_routes_or_selectors():
    source_root = Path(__file__).resolve().parents[1] / "src" / "scout_pilot" / "navigation"
    forbidden = ("hh.ru", "/vacancy", "/jobs", "/search", "xpath", "queryselector")

    for path in source_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8").casefold()
        for term in forbidden:
            assert term not in content, f"{path} contains demo-specific navigation logic: {term}"


def _run_semantic_search_flow(profile_dir: Path, page_path: Path) -> str | None:
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=profile_dir,
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=profile_dir / "screenshots",
        )
    )

    async def scenario():
        from scout_pilot.observation import SemanticObservationEngine

        await browser.start()
        try:
            observer = SemanticObservationEngine(browser)
            runtime = DefaultToolRuntime(
                create_browser_tool_registry(),
                ToolContext(browser=browser, observation_engine=observer),
            )

            with LocalDemoServer(page_path.parent) as server:
                navigation = await runtime.execute(
                    ToolRequest("browser.navigate", {"url": server.url_for(page_path.name)})
                )
                assert navigation.success is True

                fill_request = ToolRequest(
                    "browser.fill_by_label",
                    {"label": "search", "value": "alpha"},
                )
                filled = await runtime.execute(fill_request)
                assert filled.success is True
                assert filled.status is ToolExecutionStatus.SUCCESS
                assert runtime.history[-1].arguments == {
                    "label": "search",
                    "value": "[REDACTED]",
                    "context": None,
                }

                searched = await runtime.execute(
                    ToolRequest(
                        "browser.click_by_intent",
                        {"target": "search", "role": "button"},
                    )
                )
                assert searched.success is True
                assert searched.data["transition"]["changed"] is True

                opened = await runtime.execute(
                    ToolRequest("browser.click_by_intent", {"target": "alpha"})
                )
                assert opened.success is True

                state = await browser.current_state()
                return state.title
        finally:
            await browser.stop()

    return asyncio.run(scenario())


def _run_dynamic_popup_flow(profile_dir: Path, page_path: Path) -> str | None:
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=profile_dir,
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=profile_dir / "screenshots",
        )
    )

    async def scenario():
        from scout_pilot.observation import SemanticObservationEngine

        await browser.start()
        try:
            observer = SemanticObservationEngine(browser)
            runtime = DefaultToolRuntime(
                create_browser_tool_registry(),
                ToolContext(browser=browser, observation_engine=observer),
            )

            with LocalDemoServer(page_path.parent) as server:
                navigation = await runtime.execute(
                    ToolRequest("browser.navigate", {"url": server.url_for(page_path.name)})
                )
                assert navigation.success is True

                opened = await runtime.execute(
                    ToolRequest(
                        "browser.click_by_intent",
                        {"target": "show options", "role": "button"},
                    )
                )
                assert opened.success is True
                assert opened.data["transition"]["changed"] is True
                assert opened.data["transition"]["reason"] in {
                    "main_content_changed",
                    "semantic_state_changed",
                }

                continued = await runtime.execute(
                    ToolRequest(
                        "browser.click_by_intent",
                        {"target": "Continue", "role": "button", "context": "dialog"},
                    )
                )
                assert continued.success is True
                assert continued.data["transition"]["changed"] is True

                state = await browser.current_state()
                return state.title
        finally:
            await browser.stop()

    return asyncio.run(scenario())


def _write_site_a(tmp_path) -> Path:
    detail = tmp_path / "alpha-a.html"
    detail.write_text(
        "<!doctype html><title>Alpha A</title><main><h1>Alpha record</h1></main>",
        encoding="utf-8",
    )
    page = tmp_path / "site-a.html"
    page.write_text(
        f"""
        <!doctype html>
        <title>Directory A</title>
        <main>
          <label for="q">Search catalog</label>
          <input id="q" type="search" placeholder="Search catalog">
          <button type="button" onclick="document.title='Results A'; document.getElementById('results').hidden=false">Search</button>
          <section id="results" hidden>
            <a href="alpha-a.html">Alpha record</a>
          </section>
        </main>
        """,
        encoding="utf-8",
    )
    return page


def _write_site_b(tmp_path) -> Path:
    detail = tmp_path / "alpha-b.html"
    detail.write_text(
        "<!doctype html><title>Alpha B</title><main><h1>Alpha record</h1></main>",
        encoding="utf-8",
    )
    page = tmp_path / "site-b.html"
    page.write_text(
        f"""
        <!doctype html>
        <title>Directory B</title>
        <main>
          <div role="search">
            <input aria-label="Find records" name="term">
            <input type="button" value="Find" onclick="document.title='Results B'; document.getElementById('cards').hidden=false">
          </div>
          <article id="cards" hidden>
            <p>Alpha record is available.</p>
            <button type="button" aria-label="Open Alpha" onclick="window.location.href='alpha-b.html'">View</button>
          </article>
        </main>
        """,
        encoding="utf-8",
    )
    return page


def _write_popup_site(tmp_path) -> Path:
    page = tmp_path / "popup.html"
    page.write_text(
        """
        <!doctype html>
        <title>Popup fixture</title>
        <main>
          <h1>Dynamic page</h1>
          <button type="button" onclick="document.getElementById('popup').hidden=false">Show options</button>
          <section id="popup" role="dialog" aria-label="Dialog" hidden>
            <p>Dialog content appears without navigation.</p>
            <button type="button" onclick="document.title='Popup complete'">Continue</button>
          </section>
        </main>
        """,
        encoding="utf-8",
    )
    return page


class StaleFakeBrowser:
    def __init__(self):
        self.clicked_ids = []

    async def click_by_semantic_id(self, element_id):
        self.clicked_ids.append(element_id)
        if element_id == "el_old":
            return BrowserActionResult(
                action="click_by_semantic_id",
                success=False,
                message="Missing.",
                error_code="semantic_element_not_found",
            )
        return BrowserActionResult(
            action="click_by_semantic_id",
            success=True,
            message="Clicked.",
            title="Recovered",
            url="https://example.test/recovered",
        )


class StaleObservationEngine:
    def __init__(self):
        self.calls = 0

    async def observe(self):
        self.calls += 1
        element_id = "el_old" if self.calls == 1 else "el_new"
        return PageObservation(
            url="https://example.test",
            title="Before",
            summary=f"Observation {self.calls}.",
            interactive_elements=[
                InteractiveElement(
                    element_id=element_id,
                    role="button",
                    accessible_name="Continue",
                    visible_text="Continue",
                    state=ElementState(),
                )
            ],
        )
