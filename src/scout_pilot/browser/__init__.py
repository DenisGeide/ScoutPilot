"""Browser Engine layer."""

from scout_pilot.browser.engine import BrowserEngine, BrowserSession
from scout_pilot.browser.playwright_engine import PlaywrightBrowserEngine
from scout_pilot.browser.types import (
    BrowserActionResult,
    BrowserDialogSnapshot,
    BrowserElementLocation,
    BrowserElementState,
    BrowserEngineConfig,
    BrowserEngineError,
    BrowserFocusedElementSnapshot,
    BrowserFormFieldSnapshot,
    BrowserInteractiveElementSnapshot,
    BrowserPageSnapshot,
    BrowserSessionInfo,
    BrowserState,
    BrowserSectionSnapshot,
    ScreenshotResult,
)

__all__ = [
    "BrowserActionResult",
    "BrowserDialogSnapshot",
    "BrowserElementLocation",
    "BrowserElementState",
    "BrowserEngine",
    "BrowserEngineConfig",
    "BrowserEngineError",
    "BrowserFocusedElementSnapshot",
    "BrowserFormFieldSnapshot",
    "BrowserInteractiveElementSnapshot",
    "BrowserPageSnapshot",
    "BrowserSession",
    "BrowserSessionInfo",
    "BrowserState",
    "BrowserSectionSnapshot",
    "PlaywrightBrowserEngine",
    "ScreenshotResult",
]
