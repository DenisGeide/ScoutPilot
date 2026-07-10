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
