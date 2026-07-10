"""Playwright-backed Browser Engine implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from scout_pilot.browser.types import (
    BrowserActionResult,
    BrowserEngineConfig,
    BrowserEngineError,
    BrowserSessionInfo,
    BrowserState,
    ScreenshotResult,
)


SUPPORTED_URL_SCHEMES = {"http", "https", "file", "about"}


class PlaywrightBrowserEngine:
    """Browser Engine that isolates all direct Playwright communication."""

    def __init__(self, settings: BrowserEngineConfig | None = None) -> None:
        self._settings = settings or BrowserEngineConfig()
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._session: BrowserSessionInfo | None = None

    async def start(self) -> BrowserSessionInfo:
        """Start a persistent browser context and return public session metadata."""

        if self._session is not None:
            return self._session

        self._prepare_private_directory(self._settings.user_data_dir)
        try:
            self._playwright = await async_playwright().start()
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._settings.user_data_dir),
                headless=self._settings.headless,
                timeout=self._settings.default_timeout_ms,
                slow_mo=self._settings.slow_mo_ms,
                viewport={
                    "width": self._settings.viewport_width,
                    "height": self._settings.viewport_height,
                },
            )
            self._context.set_default_timeout(self._settings.default_timeout_ms)
            self._context.set_default_navigation_timeout(
                self._settings.navigation_timeout_ms
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            self._session = BrowserSessionInfo.create(self._settings)
            return self._session
        except PlaywrightError as exc:
            await self.stop()
            raise BrowserEngineError(f"Failed to start browser: {exc}") from exc

    async def stop(self) -> None:
        """Close browser resources. Calling this method repeatedly is safe."""

        context = self._context
        playwright = self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        self._session = None

        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()

    async def navigate_to(self, url: str) -> BrowserActionResult:
        """Navigate to a user-provided or discovered URL."""

        if not _is_supported_url(url):
            return BrowserActionResult(
                action="navigate_to",
                success=False,
                message="URL is empty or uses an unsupported scheme.",
                error_code="invalid_url",
            )

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("navigate_to")

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            return await self._successful_action("navigate_to", "Navigation completed.")
        except PlaywrightTimeoutError as exc:
            return await self._failed_action("navigate_to", "navigation_timeout", exc)
        except PlaywrightError as exc:
            return await self._failed_action("navigate_to", "navigation_error", exc)

    async def reload(self) -> BrowserActionResult:
        """Reload the current page."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("reload")

        try:
            await page.reload(
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            return await self._successful_action("reload", "Page reloaded.")
        except PlaywrightTimeoutError as exc:
            return await self._failed_action("reload", "reload_timeout", exc)
        except PlaywrightError as exc:
            return await self._failed_action("reload", "reload_error", exc)

    async def go_back(self) -> BrowserActionResult:
        """Move to the previous browser history entry."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("go_back")

        try:
            await page.go_back(
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            return await self._successful_action("go_back", "Back navigation completed.")
        except PlaywrightTimeoutError as exc:
            return await self._failed_action("go_back", "back_timeout", exc)
        except PlaywrightError as exc:
            return await self._failed_action("go_back", "back_error", exc)

    async def go_forward(self) -> BrowserActionResult:
        """Move to the next browser history entry."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("go_forward")

        try:
            await page.go_forward(
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            return await self._successful_action("go_forward", "Forward navigation completed.")
        except PlaywrightTimeoutError as exc:
            return await self._failed_action("go_forward", "forward_timeout", exc)
        except PlaywrightError as exc:
            return await self._failed_action("go_forward", "forward_error", exc)

    async def current_state(self) -> BrowserState:
        """Return URL and title without exposing raw browser objects."""

        page = self._get_page_or_none()
        if page is None or self._session is None:
            return BrowserState(is_started=False)

        try:
            return BrowserState(
                is_started=True,
                url=page.url,
                title=await page.title(),
                session_id=self._session.session_id,
            )
        except PlaywrightError:
            return BrowserState(is_started=False)

    async def screenshot(self, path: Path | None = None) -> ScreenshotResult:
        """Capture a PNG screenshot for diagnostics and reports."""

        page = self._get_page_or_none()
        if page is None:
            return ScreenshotResult(
                success=False,
                path=None,
                message="Browser is not started.",
                error_code="browser_not_started",
            )

        target = path or self._default_screenshot_path()
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            await page.screenshot(path=str(target), full_page=True)
            state = await self.current_state()
            return ScreenshotResult(
                success=True,
                path=target,
                message="Screenshot captured.",
                url=state.url,
                title=state.title,
            )
        except PlaywrightError as exc:
            state = await self.current_state()
            return ScreenshotResult(
                success=False,
                path=target,
                message=str(exc),
                url=state.url,
                title=state.title,
                error_code="screenshot_error",
            )

    def _get_page_or_none(self) -> Any | None:
        if self._page is not None:
            return self._page
        if self._context is None:
            return None
        if self._context.pages:
            self._page = self._context.pages[0]
            return self._page
        return None

    async def _successful_action(self, action: str, message: str) -> BrowserActionResult:
        state = await self.current_state()
        return BrowserActionResult(
            action=action,
            success=True,
            message=message,
            url=state.url,
            title=state.title,
        )

    async def _failed_action(
        self,
        action: str,
        error_code: str,
        exc: Exception,
    ) -> BrowserActionResult:
        state = await self.current_state()
        return BrowserActionResult(
            action=action,
            success=False,
            message=str(exc),
            url=state.url,
            title=state.title,
            error_code=error_code,
        )

    def _default_screenshot_path(self) -> Path:
        session_id = self._session.session_id if self._session else "no-session"
        return self._settings.screenshots_dir / f"{session_id}.png"

    @staticmethod
    def _prepare_private_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        ignore_file = path / ".gitignore"
        if not ignore_file.exists():
            ignore_file.write_text("*\n!.gitignore\n", encoding="utf-8")


def _not_started_result(action: str) -> BrowserActionResult:
    return BrowserActionResult(
        action=action,
        success=False,
        message="Browser is not started.",
        error_code="browser_not_started",
    )


def _is_supported_url(url: str) -> bool:
    candidate = url.strip()
    if not candidate:
        return False

    parsed = urlparse(candidate)
    if parsed.scheme not in SUPPORTED_URL_SCHEMES:
        return False
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        return False
    return True
