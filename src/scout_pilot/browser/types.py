"""Public Browser Engine data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from scout_pilot.config import AppConfig


@dataclass(frozen=True)
class BrowserEngineConfig:
    """Configuration for the Playwright-backed browser engine."""

    user_data_dir: Path = Path(".browser-profiles/default")
    headless: bool = False
    default_timeout_ms: int = 10000
    navigation_timeout_ms: int = 15000
    screenshots_dir: Path = Path("reports/tmp/screenshots")
    viewport_width: int = 1280
    viewport_height: int = 900
    slow_mo_ms: int = 0

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "BrowserEngineConfig":
        return cls(
            user_data_dir=config.browser_profile_dir,
            headless=config.browser_headless,
            default_timeout_ms=config.browser_default_timeout_ms,
            navigation_timeout_ms=config.browser_navigation_timeout_ms,
            screenshots_dir=config.browser_screenshots_dir,
        )

    def __post_init__(self) -> None:
        _ensure_positive(self.default_timeout_ms, "default_timeout_ms")
        _ensure_positive(self.navigation_timeout_ms, "navigation_timeout_ms")
        _ensure_positive(self.viewport_width, "viewport_width")
        _ensure_positive(self.viewport_height, "viewport_height")
        if self.slow_mo_ms < 0:
            raise ValueError("slow_mo_ms cannot be negative")


@dataclass(frozen=True)
class BrowserSessionInfo:
    """Stable public session metadata."""

    session_id: str
    user_data_dir: Path
    started_at: datetime
    headless: bool

    @classmethod
    def create(cls, settings: BrowserEngineConfig) -> "BrowserSessionInfo":
        return cls(
            session_id=uuid4().hex,
            user_data_dir=settings.user_data_dir,
            started_at=datetime.now(tz=timezone.utc),
            headless=settings.headless,
        )


@dataclass(frozen=True)
class BrowserActionResult:
    """Structured outcome for high-level browser actions."""

    action: str
    success: bool
    message: str
    url: str | None = None
    title: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class BrowserState:
    """Current browser state exposed to higher layers."""

    is_started: bool
    url: str | None = None
    title: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ScreenshotResult:
    """Structured screenshot result for diagnostics and reports."""

    success: bool
    path: Path | None
    message: str
    url: str | None = None
    title: str | None = None
    error_code: str | None = None


class BrowserEngineError(RuntimeError):
    """Controlled Browser Engine startup or lifecycle failure."""


def _ensure_positive(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
