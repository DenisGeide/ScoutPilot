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


@dataclass(frozen=True)
class BrowserElementLocation:
    """Viewport-relative element location captured by Browser Engine."""

    region: str
    x_ratio: float | None = None
    y_ratio: float | None = None
    width_ratio: float | None = None
    height_ratio: float | None = None


@dataclass(frozen=True)
class BrowserElementState:
    """Sanitized state captured from an element."""

    disabled: bool = False
    checked: bool | None = None
    expanded: bool | None = None
    pressed: bool | None = None
    selected: bool | None = None
    required: bool = False
    readonly: bool = False


@dataclass(frozen=True)
class BrowserSectionSnapshot:
    """Sanitized visible section captured from the current page."""

    role: str
    heading: str | None
    text: str
    location: BrowserElementLocation | None = None


@dataclass(frozen=True)
class BrowserInteractiveElementSnapshot:
    """Sanitized interactive element captured from the current page."""

    role: str
    accessible_name: str | None
    visible_text: str | None
    state: BrowserElementState
    location: BrowserElementLocation | None = None
    target_url: str | None = None
    input_type: str | None = None


@dataclass(frozen=True)
class BrowserFormFieldSnapshot:
    """Sanitized form field summary without the field value."""

    role: str
    input_type: str | None
    label: str | None
    placeholder: str | None
    value_state: str
    state: BrowserElementState
    location: BrowserElementLocation | None = None
    field_name: str | None = None


@dataclass(frozen=True)
class BrowserFocusedElementSnapshot:
    """Sanitized focused element summary."""

    role: str
    accessible_name: str | None
    visible_text: str | None
    input_type: str | None = None
    value_state: str | None = None


@dataclass(frozen=True)
class BrowserDialogSnapshot:
    """Sanitized visible dialog or modal summary."""

    role: str
    title: str | None
    text: str
    location: BrowserElementLocation | None = None


@dataclass(frozen=True)
class BrowserPageSnapshot:
    """Sanitized page snapshot for the Semantic Observation Engine."""

    url: str | None
    title: str | None
    origin: str | None
    load_state: str
    is_visible: bool
    viewport_width: int | None = None
    viewport_height: int | None = None
    sections: tuple[BrowserSectionSnapshot, ...] = ()
    interactive_elements: tuple[BrowserInteractiveElementSnapshot, ...] = ()
    form_fields: tuple[BrowserFormFieldSnapshot, ...] = ()
    focused_element: BrowserFocusedElementSnapshot | None = None
    dialogs: tuple[BrowserDialogSnapshot, ...] = ()
    issues: tuple[str, ...] = ()


class BrowserEngineError(RuntimeError):
    """Controlled Browser Engine startup or lifecycle failure."""


def _ensure_positive(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
