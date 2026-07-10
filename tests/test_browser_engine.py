import asyncio

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine


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
