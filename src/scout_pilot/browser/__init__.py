"""Browser Engine layer."""

from scout_pilot.browser.engine import BrowserEngine, BrowserSession
from scout_pilot.browser.playwright_engine import PlaywrightBrowserEngine
from scout_pilot.browser.types import (
    BrowserActionResult,
    BrowserEngineConfig,
    BrowserEngineError,
    BrowserSessionInfo,
    BrowserState,
    ScreenshotResult,
)

__all__ = [
    "BrowserActionResult",
    "BrowserEngine",
    "BrowserEngineConfig",
    "BrowserEngineError",
    "BrowserSession",
    "BrowserSessionInfo",
    "BrowserState",
    "PlaywrightBrowserEngine",
    "ScreenshotResult",
]
