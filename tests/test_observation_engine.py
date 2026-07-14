import asyncio

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.browser.types import BrowserPageSnapshot
from scout_pilot.models import PageIssueCode
from scout_pilot.observation import ObservationSettings, SemanticObservationEngine


def test_observation_summarizes_roles_forms_focus_and_dialog_without_values(tmp_path):
    html = """
    <!doctype html>
    <html>
      <head><title>Search Form</title></head>
      <body>
        <main>
          <h1>Vacancy search</h1>
          <p>Find relevant Python automation roles.</p>
          <a href="https://example.test/jobs">Open jobs</a>
          <form>
            <label for="query">Search query</label>
            <input id="query" name="query" value="private-query" placeholder="Role or skill">
            <label for="password">Password</label>
            <input id="password" type="password" value="super-secret" placeholder="Password">
            <button aria-label="Run search">Search</button>
          </form>
        </main>
        <dialog open aria-label="Confirmation dialog">
          <h2>Confirm filters</h2>
          <p>Filters are ready.</p>
        </dialog>
        <input id="focus-target" aria-label="Focused field" value="hidden-focus-token">
        <script>
          window.addEventListener("DOMContentLoaded", () => {
            document.getElementById("focus-target").focus();
          });
        </script>
      </body>
    </html>
    """

    observation = _observe_html(tmp_path, html)
    context = observation.to_llm_context()
    context_text = str(context)

    assert observation.metadata is not None
    assert observation.metadata.title == "Search Form"
    assert observation.metadata.origin == "file://"
    assert observation.metadata.load_state in {"interactive", "complete"}
    assert observation.metadata.is_visible is True

    element_roles = {element.role for element in observation.interactive_elements}
    assert "link" in element_roles
    assert "button" in element_roles
    assert {element.element_id for element in observation.interactive_elements}
    assert all(element.element_id.startswith("el_") for element in observation.interactive_elements)

    field_labels = {field.label for field in observation.form_fields}
    assert "Search query" in field_labels
    assert "Password" in field_labels
    assert {field.value_state for field in observation.form_fields} >= {"filled", "redacted_filled"}
    assert observation.focused_element is not None
    assert observation.focused_element.accessible_name == "Focused field"
    assert observation.focused_element.value_state == "filled"
    assert observation.dialogs
    assert observation.dialogs[0].title == "Confirm filters"

    assert "private-query" not in context_text
    assert "super-secret" not in context_text
    assert "hidden-focus-token" not in context_text
    assert "<form" not in context_text
    assert "<button" not in context_text


def test_observation_ids_are_stable_for_same_page(tmp_path):
    html = """
    <!doctype html>
    <title>Stable IDs</title>
    <main>
      <h1>Stable page</h1>
      <button aria-label="Primary action">Run</button>
      <a href="https://example.test/details">Details</a>
    </main>
    """

    first = _observe_html(tmp_path, html)
    second = _observe_html(tmp_path, html)

    assert [item.element_id for item in first.interactive_elements] == [
        item.element_id for item in second.interactive_elements
    ]


def test_oversized_page_is_bounded_and_reports_truncation(tmp_path):
    sections = "\n".join(
        f"<section><h2>Section {index}</h2><p>{'Long text ' * 40}</p>"
        f"<button>Action {index}</button></section>"
        for index in range(30)
    )
    html = f"<!doctype html><title>Large</title><body>{sections}</body>"

    observation = _observe_html(
        tmp_path,
        html,
        settings=ObservationSettings(
            max_sections=2,
            max_interactive_elements=3,
            max_form_fields=2,
            max_dialogs=1,
            max_section_chars=120,
            max_total_chars=2500,
        ),
    )

    assert len(observation.sections) <= 2
    assert len(observation.interactive_elements) <= 3
    assert len(str(observation.to_llm_context())) <= 2500
    assert PageIssueCode.OBSERVATION_TRUNCATED in {issue.code for issue in observation.issues}


def test_oversized_page_keeps_main_results_before_header_navigation(tmp_path):
    navigation = "".join(
        f'<a href="https://example.test/nav/{index}">Menu item {index}</a>' for index in range(30)
    )
    results = "".join(
        f'<a href="https://example.test/details/{index}">AI Engineer result {index} with salary</a>'
        for index in range(3)
    )
    html = (
        "<!doctype html><title>Prioritized results</title>"
        f"<header><nav>{navigation}</nav></header>"
        f"<main><h1>Search results</h1>{results}</main>"
    )

    observation = _observe_html(
        tmp_path,
        html,
        settings=ObservationSettings(
            max_sections=3,
            max_interactive_elements=5,
            max_form_fields=2,
            max_dialogs=1,
            max_section_chars=300,
            max_total_chars=3500,
        ),
    )

    names = [
        element.accessible_name or element.visible_text or ""
        for element in observation.interactive_elements
    ]
    assert observation.sections[0].role == "main"
    assert any("AI Engineer result" in name for name in names)
    assert len(str(observation.to_llm_context())) <= 3500


def test_repeated_navigation_and_footer_content_is_deduplicated(tmp_path):
    html = """
    <!doctype html>
    <title>Repeated</title>
    <nav><a href="https://example.test/home">Home</a><a href="https://example.test/jobs">Jobs</a></nav>
    <nav><a href="https://example.test/home">Home</a><a href="https://example.test/jobs">Jobs</a></nav>
    <main><h1>Unique content</h1><p>Only this main content should remain unique.</p></main>
    <footer>Contact Support</footer>
    <footer>Contact Support</footer>
    """

    observation = _observe_html(tmp_path, html)

    section_texts = [section.text for section in observation.sections]
    link_targets = [
        element.target_url for element in observation.interactive_elements if element.role == "link"
    ]

    assert sum("Home Jobs" in text for text in section_texts) == 1
    assert sum("Contact Support" in text for text in section_texts) == 1
    assert link_targets.count("https://example.test/home") == 1
    assert link_targets.count("https://example.test/jobs") == 1


def test_empty_page_reports_empty_issue(tmp_path):
    observation = _observe_html(tmp_path, "<!doctype html><title>Empty</title><body></body>")

    assert PageIssueCode.EMPTY_PAGE in {issue.code for issue in observation.issues}


def test_observation_classifies_common_blockers_without_raw_html(tmp_path):
    html = """
    <!doctype html>
    <title>Blocked fixture</title>
    <main>
      <section>
        <h1>Verify access</h1>
        <p>Verify you are human before continuing. CAPTCHA check is required.</p>
      </section>
      <section>
        <h2>Cookie settings</h2>
        <p>We use cookies for privacy settings.</p>
        <button>Accept all</button>
      </section>
      <section>
        <h2>Location</h2>
        <p>Select your region or city to continue.</p>
      </section>
      <form>
        <label for="password">Password</label>
        <input id="password" type="password" value="private-password">
        <button>Sign in</button>
      </form>
    </main>
    <dialog open aria-label="Information modal">
      <p>Modal dialog text. Select your region or city to continue.</p>
    </dialog>
    """

    observation = _observe_html(tmp_path, html)
    codes = {issue.code for issue in observation.issues}
    serialized = str(observation.to_llm_context()).casefold()

    assert PageIssueCode.MODAL_DIALOG in codes
    assert PageIssueCode.COOKIE_BANNER in codes
    assert PageIssueCode.LOGIN_WALL in codes
    assert PageIssueCode.CAPTCHA_BLOCKING_PAGE in codes
    assert PageIssueCode.REGION_PROMPT in codes
    assert PageIssueCode.BLOCKED_PAGE in codes
    assert "<dialog" not in serialized
    assert "<button" not in serialized
    assert "private-password" not in serialized


def test_non_blocking_location_control_is_not_a_region_prompt(tmp_path):
    html = """
    <!doctype html>
    <title>Results with map</title>
    <main>
      <h1>Search results</h1>
      <button>Use your location</button>
      <article><a href="/items/1001">AI Engineer</a></article>
    </main>
    """

    observation = _observe_html(tmp_path, html)

    assert PageIssueCode.REGION_PROMPT not in {issue.code for issue in observation.issues}


def test_navigation_login_link_does_not_turn_an_article_into_a_login_wall(tmp_path):
    html = """
    <!doctype html>
    <title>Help article</title>
    <header><nav><a href="/login">Sign in</a></nav></header>
    <main>
      <h1>Working with search results</h1>
      <article>
        This article explains how result pages and filters work for registered and guest users.
      </article>
    </main>
    """

    observation = _observe_html(tmp_path, html)

    assert PageIssueCode.LOGIN_WALL not in {issue.code for issue in observation.issues}


def test_robotics_content_is_not_misclassified_as_captcha(tmp_path):
    html = """
    <!doctype html>
    <title>Robotics Software Engineer</title>
    <main>
      <h1>Robotics Software Engineer</h1>
      <p>Develop robot navigation software and automated warehouse systems.</p>
    </main>
    """

    observation = _observe_html(tmp_path, html)

    assert PageIssueCode.CAPTCHA_BLOCKING_PAGE not in {issue.code for issue in observation.issues}


def test_loading_snapshot_reports_loading_issue():
    engine = SemanticObservationEngine(
        FakeSnapshotBrowser(
            BrowserPageSnapshot(
                url="https://example.test/loading",
                title="Loading",
                origin="https://example.test",
                load_state="loading",
                is_visible=True,
                issues=("loading",),
            )
        )
    )

    observation = asyncio.run(engine.observe())

    assert PageIssueCode.LOADING in {issue.code for issue in observation.issues}


def _observe_html(
    tmp_path,
    html: str,
    settings: ObservationSettings | None = None,
):
    page_path = tmp_path / "page.html"
    page_path.write_text(html, encoding="utf-8")
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=tmp_path / "screenshots",
        )
    )
    engine = SemanticObservationEngine(browser, settings=settings)

    async def scenario():
        await browser.start()
        result = await browser.navigate_to(page_path.resolve().as_uri())
        assert result.success is True
        try:
            return await engine.observe()
        finally:
            await browser.stop()

    return asyncio.run(scenario())


class FakeSnapshotBrowser:
    def __init__(self, snapshot: BrowserPageSnapshot):
        self.snapshot = snapshot

    async def capture_semantic_snapshot(self) -> BrowserPageSnapshot:
        return self.snapshot
