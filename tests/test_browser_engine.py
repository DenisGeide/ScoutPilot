import asyncio

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.observation import SemanticObservationEngine


def test_browser_navigates_local_page_and_captures_screenshot(tmp_path):
    page_path = tmp_path / "synthetic.html"
    page_path.write_text(
        "<!doctype html><title>Synthetic Page</title><h1>Local test page</h1>",
        encoding="utf-8",
    )
    profile_dir = tmp_path / "profile"
    screenshot_dir = tmp_path / "screenshots"
    settings = BrowserEngineConfig(
        user_data_dir=profile_dir,
        headless=True,
        default_timeout_ms=10000,
        navigation_timeout_ms=10000,
        screenshots_dir=screenshot_dir,
    )
    engine = PlaywrightBrowserEngine(settings)

    async def scenario():
        session = await engine.start()
        assert session.headless is True
        assert session.user_data_dir == profile_dir
        assert (profile_dir / ".gitignore").exists()

        result = await engine.navigate_to(page_path.resolve().as_uri())
        assert result.success is True
        assert result.title == "Synthetic Page"

        state = await engine.current_state()
        assert state.is_started is True
        assert state.title == "Synthetic Page"
        assert state.session_id == session.session_id

        screenshot = await engine.screenshot()
        assert screenshot.success is True
        assert screenshot.path is not None
        assert screenshot.path.exists()
        assert screenshot.path.parent == screenshot_dir

        await engine.stop()
        stopped_state = await engine.current_state()
        assert stopped_state.is_started is False

    asyncio.run(scenario())


def test_browser_navigation_errors_are_structured(tmp_path):
    settings = BrowserEngineConfig(user_data_dir=tmp_path / "profile", headless=True)
    engine = PlaywrightBrowserEngine(settings)

    async def scenario():
        await engine.start()
        result = await engine.navigate_to("not a url")
        assert result.success is False
        assert result.error_code == "invalid_url"
        await engine.stop()

    asyncio.run(scenario())


def test_browser_actions_before_start_are_structured_failures(tmp_path):
    settings = BrowserEngineConfig(user_data_dir=tmp_path / "profile", headless=True)
    engine = PlaywrightBrowserEngine(settings)

    async def scenario():
        result = await engine.reload()
        assert result.success is False
        assert result.error_code == "browser_not_started"

        screenshot = await engine.screenshot()
        assert screenshot.success is False
        assert screenshot.error_code == "browser_not_started"

    asyncio.run(scenario())


def test_browser_stop_attempts_playwright_cleanup_after_context_close_error(tmp_path):
    settings = BrowserEngineConfig(user_data_dir=tmp_path / "profile", headless=True)
    engine = PlaywrightBrowserEngine(settings)
    playwright = FakePlaywright()
    engine._context = BrokenContext()
    engine._playwright = playwright

    async def scenario():
        await engine.stop()
        state = await engine.current_state()
        return state

    state = asyncio.run(scenario())

    assert playwright.stopped is True
    assert state.is_started is False


def test_browser_dismisses_unexpected_dialog_and_reports_signal(tmp_path):
    page_path = tmp_path / "dialog.html"
    page_path.write_text(
        """
        <!doctype html>
        <title>Dialog Page</title>
        <main>
          <button aria-label="Show dialog" onclick="alert('Stop here')">Show</button>
        </main>
        """,
        encoding="utf-8",
    )
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=tmp_path / "screenshots",
        )
    )
    observer = SemanticObservationEngine(browser)

    async def scenario():
        await browser.start()
        try:
            result = await browser.navigate_to(page_path.resolve().as_uri())
            assert result.success is True
            observation = await observer.observe()
            button = next(
                item
                for item in observation.interactive_elements
                if item.accessible_name == "Show dialog"
            )

            click_result = await browser.click_by_semantic_id(button.element_id)
            assert click_result.success is True
            await browser.wait_for_timeout(50)
            snapshot = await browser.capture_semantic_snapshot()
            return snapshot
        finally:
            await browser.stop()

    snapshot = asyncio.run(scenario())

    assert "unexpected_dialog" in snapshot.issues
    assert any("Stop here" in dialog.text for dialog in snapshot.dialogs)


def test_browser_engine_public_api_does_not_expose_raw_playwright_objects():
    public_names = {
        name
        for name in dir(PlaywrightBrowserEngine)
        if not name.startswith("_")
    }

    assert "page" not in public_names
    assert "context" not in public_names
    assert "browser" not in public_names
    assert "html" not in public_names
    assert "content" not in public_names


def test_browser_clicks_and_fills_by_semantic_ids(tmp_path):
    page_path = tmp_path / "semantic-actions.html"
    page_path.write_text(
        """
        <!doctype html>
        <title>Semantic Actions</title>
        <main>
          <label for="name">Name</label>
          <input id="name" name="name" placeholder="Name">
          <button aria-label="Set title" onclick="document.title = 'Clicked'">Go</button>
        </main>
        """,
        encoding="utf-8",
    )
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=tmp_path / "screenshots",
        )
    )
    observer = SemanticObservationEngine(browser)

    async def scenario():
        await browser.start()
        try:
            result = await browser.navigate_to(page_path.resolve().as_uri())
            assert result.success is True
            observation = await observer.observe()
            field = next(item for item in observation.form_fields if item.label == "Name")
            button = next(
                item
                for item in observation.interactive_elements
                if item.accessible_name == "Set title"
            )

            fill_result = await browser.fill_by_semantic_id(field.field_id, "Alice")
            assert fill_result.success is True
            filled_observation = await observer.observe()
            filled_field = next(
                item for item in filled_observation.form_fields if item.field_id == field.field_id
            )
            assert filled_field.value_state == "filled"
            assert "Alice" not in str(filled_observation.to_llm_context())

            click_result = await browser.click_by_semantic_id(button.element_id)
            assert click_result.success is True
            state = await browser.current_state()
            assert state.title == "Clicked"
        finally:
            await browser.stop()

    asyncio.run(scenario())


def test_semantic_snapshot_includes_bounded_rendered_content_below_viewport(tmp_path):
    page_path = tmp_path / "long-detail.html"
    page_path.write_text(
        """
        <!doctype html>
        <title>AI Engineer</title>
        <main>
          <h1>AI Engineer</h1>
          <p>Salary from 300000 RUB. Experience 3-6 years.</p>
          <div style="height: 1800px"></div>
          <section>
            <h2>Requirements</h2>
            <p>Production Python, LLM integration, RAG pipelines and PostgreSQL.</p>
            <input aria-label="Private note" value="must-not-leak">
          </section>
        </main>
        """,
        encoding="utf-8",
    )
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            viewport_width=800,
            viewport_height=600,
        )
    )
    observer = SemanticObservationEngine(browser)

    async def scenario():
        await browser.start()
        try:
            await browser.navigate_to(page_path.resolve().as_uri())
            return await observer.observe()
        finally:
            await browser.stop()

    observation = asyncio.run(scenario())
    section_text = " ".join(section.text for section in observation.sections)

    assert "Production Python" in section_text
    assert "RAG pipelines" in section_text
    assert "must-not-leak" not in str(observation.to_llm_context())
    assert len(str(observation.to_llm_context())) <= observation.limits["max_total_chars"]


def test_browser_adopts_each_new_tab_opened_by_semantic_link(tmp_path):
    index_path = tmp_path / "index.html"
    first_path = tmp_path / "first.html"
    second_path = tmp_path / "second.html"
    first_path.write_text(
        "<!doctype html><title>First vacancy</title><main><h1>First vacancy</h1></main>",
        encoding="utf-8",
    )
    second_path.write_text(
        "<!doctype html><title>Second vacancy</title><main><h1>Second vacancy</h1></main>",
        encoding="utf-8",
    )
    index_path.write_text(
        f"""
        <!doctype html>
        <title>Vacancies</title>
        <main>
          <a href="{first_path.resolve().as_uri()}" target="_blank">First vacancy</a>
          <a href="{second_path.resolve().as_uri()}" target="_blank">Second vacancy</a>
        </main>
        """,
        encoding="utf-8",
    )
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=tmp_path / "screenshots",
        )
    )
    observer = SemanticObservationEngine(browser)

    async def scenario():
        await browser.start()
        try:
            await browser.navigate_to(index_path.resolve().as_uri())
            observation = await observer.observe()
            first = next(
                item
                for item in observation.interactive_elements
                if item.accessible_name == "First vacancy"
            )
            first_result = await browser.click_by_semantic_id(first.element_id)
            first_state = await browser.current_state()

            back_result = await browser.go_back()
            returned_state = await browser.current_state()

            await browser.navigate_to(index_path.resolve().as_uri())
            observation = await observer.observe()
            second = next(
                item
                for item in observation.interactive_elements
                if item.accessible_name == "Second vacancy"
            )
            second_result = await browser.click_by_semantic_id(second.element_id)
            second_state = await browser.current_state()
            return (
                first_result,
                first_state,
                back_result,
                returned_state,
                second_result,
                second_state,
            )
        finally:
            await browser.stop()

    (
        first_result,
        first_state,
        back_result,
        returned_state,
        second_result,
        second_state,
    ) = asyncio.run(scenario())

    assert first_result.success is True
    assert first_result.url == first_path.resolve().as_uri()
    assert first_state.title == "First vacancy"
    assert back_result.success is True
    assert returned_state.title == "Vacancies"
    assert second_result.success is True
    assert second_result.url == second_path.resolve().as_uri()
    assert second_state.title == "Second vacancy"


def test_browser_escape_dismisses_low_risk_dom_modal(tmp_path):
    page_path = tmp_path / "modal.html"
    page_path.write_text(
        """
        <!doctype html>
        <title>Vacancies</title>
        <main><h1>Vacancy results</h1></main>
        <div role="dialog" aria-modal="true" aria-label="Feedback survey">
          <p>Why did you not respond?</p>
        </div>
        <script>
          document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
              document.querySelector('[role="dialog"]').remove();
            }
          });
        </script>
        """,
        encoding="utf-8",
    )
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=tmp_path / "profile",
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=tmp_path / "screenshots",
        )
    )
    observer = SemanticObservationEngine(browser)

    async def scenario():
        await browser.start()
        try:
            await browser.navigate_to(page_path.resolve().as_uri())
            before = await observer.observe()
            result = await browser.press_key("Escape")
            after = await observer.observe()
            return before, result, after
        finally:
            await browser.stop()

    before, result, after = asyncio.run(scenario())

    assert result.success is True
    assert before.dialogs
    assert not after.dialogs


class BrokenContext:
    async def close(self):
        raise RuntimeError("context close failed")


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True
