import asyncio
from pathlib import Path

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.browser.types import BrowserActionResult
from scout_pilot.demo import LocalDemoServer
from scout_pilot.models import (
    ElementState,
    FormFieldSummary,
    InteractiveElement,
    PageObservation,
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
                paused = await runtime.execute(fill_request)
                assert paused.status is ToolExecutionStatus.PAUSED
                confirmation = paused.data["confirmation"]
                assert runtime.confirm_pending_action(str(confirmation["confirmation_id"])) is True

                filled = await runtime.execute(fill_request)
                assert filled.success is True
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
