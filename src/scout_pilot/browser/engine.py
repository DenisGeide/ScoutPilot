"""Browser Engine protocol definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from scout_pilot.browser.types import (
    BrowserActionResult,
    BrowserPageSnapshot,
    BrowserState,
    ScreenshotResult,
)


class BrowserSession(Protocol):
    """Abstract browser session without exposing Playwright objects."""

    @property
    def session_id(self) -> str:
        """Stable identifier for this browser session."""


class BrowserEngine(Protocol):
    """Interface implemented by the future Playwright-backed browser engine."""

    async def start(self) -> BrowserSession:
        """Start a visible browser session."""

    async def stop(self) -> None:
        """Close browser resources owned by the engine."""

    async def navigate_to(self, url: str) -> BrowserActionResult:
        """Navigate to a user-provided or discovered URL."""

    async def reload(self) -> BrowserActionResult:
        """Reload the current page."""

    async def go_back(self) -> BrowserActionResult:
        """Move to the previous browser history entry."""

    async def go_forward(self) -> BrowserActionResult:
        """Move to the next browser history entry."""

    async def current_state(self) -> BrowserState:
        """Return the current URL, title and lifecycle state."""

    async def screenshot(self, path: Path | None = None) -> ScreenshotResult:
        """Capture a diagnostic screenshot without exposing page internals."""

    async def capture_semantic_snapshot(self) -> BrowserPageSnapshot:
        """Capture sanitized page data for semantic observation."""

    async def click_by_semantic_id(self, element_id: str) -> BrowserActionResult:
        """Click a visible element by its generated semantic ID."""

    async def fill_by_semantic_id(self, element_id: str, value: str) -> BrowserActionResult:
        """Fill a form field by its generated semantic ID."""

    async def press_key(self, key: str) -> BrowserActionResult:
        """Press a keyboard key on the current page."""

    async def wait_for_timeout(self, milliseconds: int) -> BrowserActionResult:
        """Wait for a short browser timeout."""
