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


class BrokenContext:
    async def close(self):
        raise RuntimeError("context close failed")


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True
