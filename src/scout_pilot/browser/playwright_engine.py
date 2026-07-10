"""Playwright-backed Browser Engine implementation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

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
from scout_pilot.semantic_ids import (
    stable_semantic_id,
    truncate_optional_semantic_text,
)


SUPPORTED_URL_SCHEMES = {"http", "https", "file", "about"}
MAX_BROWSER_WAIT_MS = 60_000
SEMANTIC_INTERACTIVE_SELECTOR = (
    "a[href],button,input,textarea,select,summary,"
    "[role='button'],[role='link'],[role='checkbox'],[role='radio'],"
    "[role='textbox'],[role='searchbox'],[role='combobox'],[role='menuitem'],[role='tab'],"
    "[tabindex]:not([tabindex='-1']),[contenteditable='true']"
)


logger = logging.getLogger(__name__)


class PlaywrightBrowserEngine:
    """Browser Engine that isolates all direct Playwright communication."""

    def __init__(self, settings: BrowserEngineConfig | None = None) -> None:
        self._settings = settings or BrowserEngineConfig()
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._session: BrowserSessionInfo | None = None
        self._last_navigation_error: str | None = None
        self._recent_dialogs: list[BrowserDialogSnapshot] = []

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
            self._context.on("page", self._install_dialog_handler)
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
            self._install_dialog_handler(self._page)
            self._session = BrowserSessionInfo.create(self._settings)
            return self._session
        except Exception as exc:
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
        self._last_navigation_error = None
        self._recent_dialogs = []

        if context is not None:
            try:
                await context.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup boundary
                logger.warning(
                    "browser_context_close_failed",
                    extra={"event": "browser_context_close_failed", "error": str(exc)},
                )
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as exc:  # pragma: no cover - defensive cleanup boundary
                logger.warning(
                    "playwright_stop_failed",
                    extra={"event": "playwright_stop_failed", "error": str(exc)},
                )

    async def navigate_to(self, url: str) -> BrowserActionResult:
        """Navigate to a user-provided or discovered URL."""

        if not _is_supported_url(url):
            self._last_navigation_error = "invalid_url"
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
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            http_failure = await self._http_status_failure("navigate_to", response)
            if http_failure is not None:
                return http_failure
            return await self._successful_action("navigate_to", "Navigation completed.")
        except PlaywrightTimeoutError as exc:
            self._last_navigation_error = "navigation_timeout"
            return await self._failed_action("navigate_to", "navigation_timeout", exc)
        except PlaywrightError as exc:
            self._last_navigation_error = "navigation_error"
            return await self._failed_action("navigate_to", "navigation_error", exc)

    async def reload(self) -> BrowserActionResult:
        """Reload the current page."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("reload")

        try:
            response = await page.reload(
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            http_failure = await self._http_status_failure("reload", response)
            if http_failure is not None:
                return http_failure
            return await self._successful_action("reload", "Page reloaded.")
        except PlaywrightTimeoutError as exc:
            self._last_navigation_error = "reload_timeout"
            return await self._failed_action("reload", "reload_timeout", exc)
        except PlaywrightError as exc:
            self._last_navigation_error = "reload_error"
            return await self._failed_action("reload", "reload_error", exc)

    async def go_back(self) -> BrowserActionResult:
        """Move to the previous browser history entry."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("go_back")

        try:
            response = await page.go_back(
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            http_failure = await self._http_status_failure("go_back", response)
            if http_failure is not None:
                return http_failure
            return await self._successful_action("go_back", "Back navigation completed.")
        except PlaywrightTimeoutError as exc:
            self._last_navigation_error = "back_timeout"
            return await self._failed_action("go_back", "back_timeout", exc)
        except PlaywrightError as exc:
            self._last_navigation_error = "back_error"
            return await self._failed_action("go_back", "back_error", exc)

    async def go_forward(self) -> BrowserActionResult:
        """Move to the next browser history entry."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("go_forward")

        try:
            response = await page.go_forward(
                wait_until="domcontentloaded",
                timeout=self._settings.navigation_timeout_ms,
            )
            http_failure = await self._http_status_failure("go_forward", response)
            if http_failure is not None:
                return http_failure
            return await self._successful_action("go_forward", "Forward navigation completed.")
        except PlaywrightTimeoutError as exc:
            self._last_navigation_error = "forward_timeout"
            return await self._failed_action("go_forward", "forward_timeout", exc)
        except PlaywrightError as exc:
            self._last_navigation_error = "forward_error"
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

    async def click_by_semantic_id(self, element_id: str) -> BrowserActionResult:
        """Click a visible element by its generated semantic ID."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("click_by_semantic_id")
        if not element_id.strip():
            return BrowserActionResult(
                action="click_by_semantic_id",
                success=False,
                message="Semantic element ID is empty.",
                error_code="invalid_semantic_id",
            )

        try:
            match = await self._find_element_by_semantic_id(element_id)
            if match is None:
                return BrowserActionResult(
                    action="click_by_semantic_id",
                    success=False,
                    message="Semantic element was not found on the current page.",
                    error_code="semantic_element_not_found",
                )
            handle, _snapshot = match
            await handle.click(timeout=self._settings.default_timeout_ms)
            return await self._successful_action(
                "click_by_semantic_id",
                "Semantic element clicked.",
            )
        except PlaywrightTimeoutError as exc:
            return await self._failed_action("click_by_semantic_id", "click_timeout", exc)
        except PlaywrightError as exc:
            return await self._failed_action(
                "click_by_semantic_id",
                _playwright_action_error_code("click_error", exc),
                exc,
            )

    async def fill_by_semantic_id(self, element_id: str, value: str) -> BrowserActionResult:
        """Fill a visible form field by its generated semantic ID."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("fill_by_semantic_id")
        if not element_id.strip():
            return BrowserActionResult(
                action="fill_by_semantic_id",
                success=False,
                message="Semantic element ID is empty.",
                error_code="invalid_semantic_id",
            )

        try:
            match = await self._find_element_by_semantic_id(element_id)
            if match is None:
                return BrowserActionResult(
                    action="fill_by_semantic_id",
                    success=False,
                    message="Semantic field was not found on the current page.",
                    error_code="semantic_element_not_found",
                )

            handle, snapshot = match
            input_type = _optional_str(snapshot.get("inputType"))
            role = _optional_str(snapshot.get("role"))
            if input_type in {"select", "select-multiple"}:
                await handle.select_option(value, timeout=self._settings.default_timeout_ms)
            elif input_type in {"checkbox", "radio"} or role in {"checkbox", "radio"}:
                normalized_value = value.strip().lower()
                if normalized_value in {"1", "true", "yes", "on", "checked"}:
                    await handle.check(timeout=self._settings.default_timeout_ms)
                elif normalized_value in {"0", "false", "no", "off", "unchecked"}:
                    await handle.uncheck(timeout=self._settings.default_timeout_ms)
                else:
                    return BrowserActionResult(
                        action="fill_by_semantic_id",
                        success=False,
                        message="Checkbox and radio fields require a boolean-like value.",
                        error_code="invalid_field_value",
                    )
            elif not _is_text_fillable(input_type, role):
                return BrowserActionResult(
                    action="fill_by_semantic_id",
                    success=False,
                    message="Semantic element is not a fillable field.",
                    error_code="element_not_fillable",
                )
            else:
                await handle.fill(value, timeout=self._settings.default_timeout_ms)
            return await self._successful_action(
                "fill_by_semantic_id",
                "Semantic field filled.",
            )
        except PlaywrightTimeoutError as exc:
            return await self._failed_action("fill_by_semantic_id", "fill_timeout", exc)
        except PlaywrightError as exc:
            return await self._failed_action(
                "fill_by_semantic_id",
                _playwright_action_error_code("fill_error", exc),
                exc,
            )

    async def press_key(self, key: str) -> BrowserActionResult:
        """Press a keyboard key on the current page."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("press_key")
        if not key.strip():
            return BrowserActionResult(
                action="press_key",
                success=False,
                message="Keyboard key is empty.",
                error_code="invalid_key",
            )

        try:
            await page.keyboard.press(key)
            return await self._successful_action("press_key", "Keyboard key pressed.")
        except PlaywrightError as exc:
            return await self._failed_action("press_key", "press_key_error", exc)

    async def wait_for_timeout(self, milliseconds: int) -> BrowserActionResult:
        """Wait for a short browser timeout."""

        page = self._get_page_or_none()
        if page is None:
            return _not_started_result("wait_for_timeout")
        if milliseconds < 0:
            return BrowserActionResult(
                action="wait_for_timeout",
                success=False,
                message="Wait duration cannot be negative.",
                error_code="invalid_wait_duration",
            )
        if milliseconds > MAX_BROWSER_WAIT_MS:
            return BrowserActionResult(
                action="wait_for_timeout",
                success=False,
                message="Wait duration exceeds the maximum allowed browser wait.",
                error_code="invalid_wait_duration",
            )

        try:
            await page.wait_for_timeout(milliseconds)
            return await self._successful_action("wait_for_timeout", "Wait completed.")
        except PlaywrightError as exc:
            return await self._failed_action("wait_for_timeout", "wait_error", exc)

    async def capture_semantic_snapshot(self) -> BrowserPageSnapshot:
        """Capture sanitized page data for semantic observation."""

        page = self._get_page_or_none()
        if page is None:
            return BrowserPageSnapshot(
                url=None,
                title=None,
                origin=None,
                load_state="not_started",
                is_visible=False,
                issues=("browser_not_started",),
            )

        try:
            raw_snapshot = await page.locator(":root").evaluate(
                _SEMANTIC_SNAPSHOT_SCRIPT,
                {
                    "maxSections": 32,
                    "maxInteractive": 120,
                    "maxFields": 80,
                    "maxDialogs": 10,
                    "maxSectionTextChars": 1400,
                    "maxElementTextChars": 240,
                },
            )
            snapshot = _snapshot_from_raw(raw_snapshot)
            snapshot = self._snapshot_with_runtime_signals(snapshot)
            if self._last_navigation_error is None:
                return snapshot
            return BrowserPageSnapshot(
                url=snapshot.url,
                title=snapshot.title,
                origin=snapshot.origin,
                load_state=snapshot.load_state,
                is_visible=snapshot.is_visible,
                viewport_width=snapshot.viewport_width,
                viewport_height=snapshot.viewport_height,
                sections=snapshot.sections,
                interactive_elements=snapshot.interactive_elements,
                form_fields=snapshot.form_fields,
                focused_element=snapshot.focused_element,
                dialogs=snapshot.dialogs,
                issues=tuple(dict.fromkeys((*snapshot.issues, self._last_navigation_error))),
            )
        except PlaywrightError as exc:
            state = await self.current_state()
            return BrowserPageSnapshot(
                url=state.url,
                title=state.title,
                origin=_origin_from_url(state.url),
                load_state="unknown",
                is_visible=False,
                issues=tuple(
                    dict.fromkeys(
                        (
                            "observation_error",
                            *((self._last_navigation_error,) if self._last_navigation_error else ()),
                        )
                    )
                ),
            )

    async def _find_element_by_semantic_id(
        self,
        element_id: str,
    ) -> tuple[Any, dict[str, Any]] | None:
        page = self._get_page_or_none()
        if page is None:
            return None

        handles = await page.locator(SEMANTIC_INTERACTIVE_SELECTOR).element_handles()
        for handle in handles:
            snapshot = await handle.evaluate(_SINGLE_ELEMENT_SEMANTIC_SCRIPT)
            if not snapshot.get("isVisible"):
                continue
            if element_id in _semantic_ids_for_element_snapshot(snapshot):
                return handle, snapshot
        return None

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
        self._last_navigation_error = None
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

    async def _http_status_failure(
        self,
        action: str,
        response: Any | None,
    ) -> BrowserActionResult | None:
        if response is None:
            return None
        status = getattr(response, "status", None)
        if not isinstance(status, int) or status < 400:
            return None

        error_code = f"http_status_{status}"
        self._last_navigation_error = error_code
        state = await self.current_state()
        return BrowserActionResult(
            action=action,
            success=False,
            message=f"Navigation completed with HTTP status {status}.",
            url=state.url,
            title=state.title,
            error_code=error_code,
        )

    def _install_dialog_handler(self, page: Any) -> None:
        page.on(
            "dialog",
            lambda dialog: asyncio.create_task(self._handle_unexpected_dialog(dialog)),
        )

    async def _handle_unexpected_dialog(self, dialog: Any) -> None:
        dialog_type = str(getattr(dialog, "type", "dialog") or "dialog")
        dialog_message = truncate_optional_semantic_text(
            str(getattr(dialog, "message", "") or ""),
            500,
        )
        self._recent_dialogs.append(
            BrowserDialogSnapshot(
                role="dialog",
                title=f"Unexpected {dialog_type} dialog",
                text=dialog_message or "",
                location=None,
            )
        )
        self._recent_dialogs = self._recent_dialogs[-5:]
        try:
            await dialog.dismiss()
        except PlaywrightError as exc:  # pragma: no cover - depends on browser timing
            logger.info(
                "unexpected_dialog_dismiss_failed",
                extra={
                    "event": "unexpected_dialog_dismiss_failed",
                    "dialog_type": dialog_type,
                    "error": str(exc),
                },
            )

    def _snapshot_with_runtime_signals(
        self,
        snapshot: BrowserPageSnapshot,
    ) -> BrowserPageSnapshot:
        if not self._recent_dialogs:
            return snapshot

        dialogs = tuple(self._recent_dialogs)
        self._recent_dialogs = []
        return BrowserPageSnapshot(
            url=snapshot.url,
            title=snapshot.title,
            origin=snapshot.origin,
            load_state=snapshot.load_state,
            is_visible=snapshot.is_visible,
            viewport_width=snapshot.viewport_width,
            viewport_height=snapshot.viewport_height,
            sections=snapshot.sections,
            interactive_elements=snapshot.interactive_elements,
            form_fields=snapshot.form_fields,
            focused_element=snapshot.focused_element,
            dialogs=tuple(dict.fromkeys((*dialogs, *snapshot.dialogs))),
            issues=tuple(dict.fromkeys((*snapshot.issues, "unexpected_dialog"))),
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


def _is_text_fillable(input_type: str | None, role: str | None) -> bool:
    if role in {"textbox", "combobox", "searchbox"}:
        return True
    return input_type in {
        "text",
        "search",
        "email",
        "url",
        "tel",
        "password",
        "number",
        "textarea",
        "contenteditable",
    }


def _playwright_action_error_code(default: str, exc: PlaywrightError) -> str:
    message = str(exc).casefold()
    if "not attached" in message or "detached" in message:
        return "semantic_element_stale"
    if "target page" in message and "closed" in message:
        return "browser_closed"
    return default


def _snapshot_from_raw(raw: dict[str, Any]) -> BrowserPageSnapshot:
    viewport = raw.get("viewport") or {}
    return BrowserPageSnapshot(
        url=_optional_str(raw.get("url")),
        title=_optional_str(raw.get("title")),
        origin=_optional_str(raw.get("origin")),
        load_state=_optional_str(raw.get("loadState")) or "unknown",
        is_visible=bool(raw.get("isVisible", False)),
        viewport_width=_optional_int(viewport.get("width")),
        viewport_height=_optional_int(viewport.get("height")),
        sections=tuple(_section_from_raw(item) for item in raw.get("sections", [])),
        interactive_elements=tuple(
            _interactive_from_raw(item) for item in raw.get("interactiveElements", [])
        ),
        form_fields=tuple(_field_from_raw(item) for item in raw.get("formFields", [])),
        focused_element=_focused_from_raw(raw.get("focusedElement")),
        dialogs=tuple(_dialog_from_raw(item) for item in raw.get("dialogs", [])),
        issues=tuple(_optional_str(item) or "unknown" for item in raw.get("issues", [])),
    )


def _section_from_raw(raw: dict[str, Any]) -> BrowserSectionSnapshot:
    return BrowserSectionSnapshot(
        role=_optional_str(raw.get("role")) or "section",
        heading=_optional_str(raw.get("heading")),
        text=_optional_str(raw.get("text")) or "",
        location=_location_from_raw(raw.get("location")),
    )


def _interactive_from_raw(raw: dict[str, Any]) -> BrowserInteractiveElementSnapshot:
    return BrowserInteractiveElementSnapshot(
        role=_optional_str(raw.get("role")) or "generic",
        accessible_name=_optional_str(raw.get("accessibleName")),
        visible_text=_optional_str(raw.get("visibleText")),
        state=_state_from_raw(raw.get("state")),
        location=_location_from_raw(raw.get("location")),
        target_url=_optional_str(raw.get("targetUrl")),
        input_type=_optional_str(raw.get("inputType")),
    )


def _field_from_raw(raw: dict[str, Any]) -> BrowserFormFieldSnapshot:
    return BrowserFormFieldSnapshot(
        role=_optional_str(raw.get("role")) or "textbox",
        input_type=_optional_str(raw.get("inputType")),
        label=_optional_str(raw.get("label")),
        placeholder=_optional_str(raw.get("placeholder")),
        value_state=_optional_str(raw.get("valueState")) or "unknown",
        state=_state_from_raw(raw.get("state")),
        location=_location_from_raw(raw.get("location")),
        field_name=_optional_str(raw.get("fieldName")),
    )


def _focused_from_raw(raw: dict[str, Any] | None) -> BrowserFocusedElementSnapshot | None:
    if not raw:
        return None
    return BrowserFocusedElementSnapshot(
        role=_optional_str(raw.get("role")) or "generic",
        accessible_name=_optional_str(raw.get("accessibleName")),
        visible_text=_optional_str(raw.get("visibleText")),
        input_type=_optional_str(raw.get("inputType")),
        value_state=_optional_str(raw.get("valueState")),
    )


def _dialog_from_raw(raw: dict[str, Any]) -> BrowserDialogSnapshot:
    return BrowserDialogSnapshot(
        role=_optional_str(raw.get("role")) or "dialog",
        title=_optional_str(raw.get("title")),
        text=_optional_str(raw.get("text")) or "",
        location=_location_from_raw(raw.get("location")),
    )


def _location_from_raw(raw: dict[str, Any] | None) -> BrowserElementLocation | None:
    if not raw:
        return None
    return BrowserElementLocation(
        region=_optional_str(raw.get("region")) or "unknown",
        x_ratio=_optional_float(raw.get("xRatio")),
        y_ratio=_optional_float(raw.get("yRatio")),
        width_ratio=_optional_float(raw.get("widthRatio")),
        height_ratio=_optional_float(raw.get("heightRatio")),
    )


def _state_from_raw(raw: dict[str, Any] | None) -> BrowserElementState:
    raw = raw or {}
    return BrowserElementState(
        disabled=bool(raw.get("disabled", False)),
        checked=_optional_bool(raw.get("checked")),
        expanded=_optional_bool(raw.get("expanded")),
        pressed=_optional_bool(raw.get("pressed")),
        selected=_optional_bool(raw.get("selected")),
        required=bool(raw.get("required", False)),
        readonly=bool(raw.get("readonly", False)),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _origin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return "file://"
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme:
        return f"{parsed.scheme}:"
    return None


def _semantic_ids_for_element_snapshot(snapshot: dict[str, Any]) -> tuple[str, ...]:
    role = _optional_str(snapshot.get("role")) or "generic"
    accessible_name = truncate_optional_semantic_text(
        _optional_str(snapshot.get("accessibleName")),
        160,
    )
    visible_text = truncate_optional_semantic_text(
        _optional_str(snapshot.get("visibleText")),
        160,
    )
    target_url = _optional_str(snapshot.get("targetUrl"))
    input_type = _optional_str(snapshot.get("inputType"))
    field_name = _optional_str(snapshot.get("fieldName"))
    placeholder = truncate_optional_semantic_text(
        _optional_str(snapshot.get("placeholder")),
        160,
    )

    ids = [
        stable_semantic_id(
            "el",
            role,
            accessible_name,
            visible_text,
            target_url,
            input_type,
        )
    ]
    if snapshot.get("isField"):
        ids.append(
            stable_semantic_id(
                "field",
                role,
                input_type,
                accessible_name,
                placeholder,
                field_name,
            )
        )
    return tuple(ids)


_SEMANTIC_SNAPSHOT_SCRIPT = """
(root, options) => {
  const doc = root.ownerDocument || document;
  const win = doc.defaultView || window;
  const body = doc.body;
  const maxSections = options.maxSections || 32;
  const maxInteractive = options.maxInteractive || 120;
  const maxFields = options.maxFields || 80;
  const maxDialogs = options.maxDialogs || 10;
  const maxSectionTextChars = options.maxSectionTextChars || 1400;
  const maxElementTextChars = options.maxElementTextChars || 240;

  const normalizeText = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const truncate = (value, limit) => {
    const text = normalizeText(value);
    return text.length > limit ? `${text.slice(0, Math.max(limit - 1, 0))}...` : text;
  };
  const isVisible = (element) => {
    if (!element || !(element instanceof Element)) return false;
    const style = win.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    return rect.bottom >= 0 && rect.right >= 0 && rect.top <= win.innerHeight && rect.left <= win.innerWidth;
  };
  const visibleText = (element) => {
    if (!element) return "";
    const children = Array.from(element.children || []).filter(isVisible);
    if (children.length === 0) return normalizeText(element.innerText || element.textContent || "");
    const ownText = normalizeText(
      Array.from(element.childNodes || [])
        .filter((node) => node.nodeType === 3)
        .map((node) => node.textContent || "")
        .join(" ")
    );
    const childText = normalizeText(children.map((child) => visibleText(child)).join(" "));
    return normalizeText([ownText, childText].filter(Boolean).join(" "));
  };
  const textOf = (element, limit = maxElementTextChars) => {
    if (!element) return null;
    return truncate(visibleText(element), limit) || null;
  };
  const headingOf = (element) => {
    const heading = Array.from(element.querySelectorAll("h1,h2,h3,h4,h5,h6")).find(isVisible);
    return heading ? textOf(heading, 160) : null;
  };
  const roleOf = (element) => {
    const explicit = normalizeText(element.getAttribute("role"));
    if (explicit) return explicit.toLowerCase();
    const tag = element.tagName.toLowerCase();
    const type = normalizeText(element.getAttribute("type")).toLowerCase();
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "button" || ["button", "submit", "reset"].includes(type)) return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "input") {
      if (["checkbox"].includes(type)) return "checkbox";
      if (["radio"].includes(type)) return "radio";
      if (["range"].includes(type)) return "slider";
      return "textbox";
    }
    if (tag === "nav") return "navigation";
    if (tag === "main") return "main";
    if (tag === "header") return "banner";
    if (tag === "footer") return "contentinfo";
    if (tag === "article") return "article";
    if (tag === "section") return "region";
    if (tag === "form") return "form";
    if (tag === "dialog") return "dialog";
    if (element.isContentEditable) return "textbox";
    return tag;
  };
  const isField = (element) => {
    const tag = element.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || element.isContentEditable;
  };
  const inputTypeOf = (element) => {
    const tag = element.tagName.toLowerCase();
    if (tag === "button") return normalizeText(element.getAttribute("type")).toLowerCase() || (element.form ? "submit" : "button");
    if (tag === "input") return normalizeText(element.getAttribute("type")).toLowerCase() || "text";
    if (tag === "textarea") return "textarea";
    if (tag === "select") return element.multiple ? "select-multiple" : "select";
    if (element.isContentEditable) return "contenteditable";
    return null;
  };
  const labelsOf = (element) => {
    try {
      if (element.labels && element.labels.length > 0) {
        return normalizeText(Array.from(element.labels).map((label) => label.innerText || label.textContent).join(" "));
      }
    } catch {
      return "";
    }
    return "";
  };
  const labelledByText = (element) => {
    const ids = normalizeText(element.getAttribute("aria-labelledby"));
    if (!ids) return "";
    return normalizeText(ids.split(" ").map((id) => {
      const label = doc.getElementById(id);
      return label ? label.innerText || label.textContent || "" : "";
    }).join(" "));
  };
  const nameOf = (element) => {
    const aria = normalizeText(element.getAttribute("aria-label"));
    if (aria) return aria;
    const labelled = labelledByText(element);
    if (labelled) return labelled;
    const labels = labelsOf(element);
    if (labels) return labels;
    const alt = normalizeText(element.getAttribute("alt"));
    if (alt) return alt;
    const title = normalizeText(element.getAttribute("title"));
    if (title) return title;
    const placeholder = normalizeText(element.getAttribute("placeholder"));
    if (placeholder) return placeholder;
    const type = inputTypeOf(element);
    if (["button", "submit", "reset"].includes(type || "")) {
      const buttonValue = normalizeText(element.getAttribute("value"));
      if (buttonValue) return buttonValue;
    }
    return textOf(element, 160);
  };
  const visibleTextOfInteractive = (element) => {
    if (isField(element)) {
      const type = inputTypeOf(element);
      if (!["button", "submit", "reset"].includes(type || "")) return null;
    }
    return textOf(element, maxElementTextChars);
  };
  const boolAttribute = (element, name) => {
    const value = element.getAttribute(name);
    if (value === null) return null;
    if (value === "true" || value === "") return true;
    if (value === "false") return false;
    return null;
  };
  const stateOf = (element) => ({
    disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
    checked: typeof element.checked === "boolean" ? Boolean(element.checked) : boolAttribute(element, "aria-checked"),
    expanded: boolAttribute(element, "aria-expanded"),
    pressed: boolAttribute(element, "aria-pressed"),
    selected: typeof element.selected === "boolean" ? Boolean(element.selected) : boolAttribute(element, "aria-selected"),
    required: Boolean(element.required || element.getAttribute("aria-required") === "true"),
    readonly: Boolean(element.readOnly || element.getAttribute("aria-readonly") === "true"),
  });
  const locationOf = (element) => {
    const rect = element.getBoundingClientRect();
    const centerX = (rect.left + rect.width / 2) / Math.max(win.innerWidth, 1);
    const centerY = (rect.top + rect.height / 2) / Math.max(win.innerHeight, 1);
    const vertical = centerY < 0.33 ? "top" : centerY > 0.66 ? "bottom" : "middle";
    const horizontal = centerX < 0.33 ? "left" : centerX > 0.66 ? "right" : "center";
    const round = (value) => Math.round(Math.max(0, Math.min(value, 1)) * 1000) / 1000;
    return {
      region: `${vertical}_${horizontal}`,
      xRatio: round(centerX),
      yRatio: round(centerY),
      widthRatio: round(rect.width / Math.max(win.innerWidth, 1)),
      heightRatio: round(rect.height / Math.max(win.innerHeight, 1)),
    };
  };
  const valueStateOf = (element) => {
    const type = inputTypeOf(element);
    if (type === "checkbox" || type === "radio") return element.checked ? "checked" : "unchecked";
    if (type === "select" || type === "select-multiple") return element.selectedIndex >= 0 ? "selected" : "empty";
    if (type === "password") return element.value ? "redacted_filled" : "empty";
    if (element.isContentEditable) return normalizeText(element.textContent).length > 0 ? "filled" : "empty";
    if ("value" in element) return element.value ? "filled" : "empty";
    return "unknown";
  };
  const sectionRoleOf = (element) => {
    const role = roleOf(element);
    return role === "div" ? "section" : role;
  };
  const sectionSelector = "main,header,nav,footer,aside,article,section,form,[role='main'],[role='navigation'],[role='region'],[role='banner'],[role='contentinfo']";
  const interactiveSelector = "a[href],button,input,textarea,select,summary,[role='button'],[role='link'],[role='checkbox'],[role='radio'],[role='textbox'],[role='searchbox'],[role='combobox'],[role='menuitem'],[role='tab'],[tabindex]:not([tabindex='-1']),[contenteditable='true']";
  const dialogSelector = "dialog[open],[role='dialog'],[role='alertdialog'],[aria-modal='true']";

  const sections = Array.from(doc.querySelectorAll(sectionSelector))
    .filter(isVisible)
    .slice(0, maxSections)
    .map((element) => ({
      role: sectionRoleOf(element),
      heading: headingOf(element),
      text: textOf(element, maxSectionTextChars) || "",
      location: locationOf(element),
    }))
    .filter((section) => section.text || section.heading);

  const interactiveElements = Array.from(doc.querySelectorAll(interactiveSelector))
    .filter(isVisible)
    .slice(0, maxInteractive)
    .map((element) => ({
      role: roleOf(element),
      accessibleName: nameOf(element),
      visibleText: visibleTextOfInteractive(element),
      state: stateOf(element),
      location: locationOf(element),
      targetUrl: element.href || null,
      inputType: inputTypeOf(element),
    }));

  const formFields = interactiveElements
    .map((_, index) => Array.from(doc.querySelectorAll(interactiveSelector)).filter(isVisible)[index])
    .filter((element) => element && isField(element))
    .slice(0, maxFields)
    .map((element) => ({
      role: roleOf(element),
      inputType: inputTypeOf(element),
      label: nameOf(element),
      placeholder: normalizeText(element.getAttribute("placeholder")) || null,
      valueState: valueStateOf(element),
      state: stateOf(element),
      location: locationOf(element),
      fieldName: normalizeText(element.getAttribute("name")) || null,
    }));

  const active = doc.activeElement && doc.activeElement !== body && isVisible(doc.activeElement)
    ? doc.activeElement
    : null;
  const focusedElement = active ? {
    role: roleOf(active),
    accessibleName: nameOf(active),
    visibleText: visibleTextOfInteractive(active),
    inputType: inputTypeOf(active),
    valueState: isField(active) ? valueStateOf(active) : null,
  } : null;

  const dialogs = Array.from(doc.querySelectorAll(dialogSelector))
    .filter(isVisible)
    .slice(0, maxDialogs)
    .map((element) => ({
      role: roleOf(element),
      title: headingOf(element) || nameOf(element),
      text: textOf(element, maxSectionTextChars) || "",
      location: locationOf(element),
    }));

  const issues = [];
  const loadState = doc.readyState || "unknown";
  const bodyText = normalizeText(body ? (body.innerText || body.textContent || "") : "");
  if (loadState !== "complete") issues.push("loading");
  if (!bodyText && interactiveElements.length === 0) issues.push("empty_page");
  const lowerBodyText = bodyText.toLowerCase();
  if (/(cookie|cookies|consent|privacy settings|accept all|reject all|файл(?:ы)? cookie|куки|согласие|конфиденциальност)/.test(lowerBodyText)) {
    issues.push("cookie_banner");
  }
  if (/(sign in|log in|login required|authentication required|create account|войдите|войти|авторизуйтесь|требуется вход|зарегистрируйтесь)/.test(lowerBodyText)) {
    issues.push("login_wall");
  }
  if (/(captcha|recaptcha|hcaptcha|verify you are human|prove you are human|unusual traffic|robot|automated requests|проверьте,? что вы человек|проверка,? что вы человек|капч|робот|автоматическ(?:ие|их) запрос)/.test(lowerBodyText)) {
    issues.push("captcha_blocking_page");
  }
  if (/(select (?:your )?(?:region|location|city|country)|choose (?:your )?(?:region|location|city|country)|allow location|use your location|geolocation|выберите (?:регион|город|страну|локацию)|определить (?:местоположение|город)|разрешить доступ к местоположению)/.test(lowerBodyText)) {
    issues.push("region_prompt");
  }
  if (/(access denied|forbidden|blocked|verify you are human|enable javascript|доступ запрещен|доступ ограничен|заблокировано|включите javascript)/.test(lowerBodyText)) {
    issues.push("blocked_page");
  }

  const origin = win.location.origin === "null" ? `${win.location.protocol}//` : win.location.origin;
  return {
    url: win.location.href,
    title: doc.title || null,
    origin,
    loadState,
    isVisible: !doc.hidden,
    viewport: { width: win.innerWidth, height: win.innerHeight },
    sections,
    interactiveElements,
    formFields,
    focusedElement,
    dialogs,
    issues,
  };
}
"""


_SINGLE_ELEMENT_SEMANTIC_SCRIPT = """
(element) => {
  const doc = element.ownerDocument || document;
  const win = doc.defaultView || window;
  const normalizeText = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const truncate = (value, limit) => {
    const text = normalizeText(value);
    return text.length > limit ? `${text.slice(0, Math.max(limit - 1, 0))}...` : text;
  };
  const isVisible = (candidate) => {
    if (!candidate || !(candidate instanceof Element)) return false;
    const style = win.getComputedStyle(candidate);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = candidate.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    return rect.bottom >= 0 && rect.right >= 0 && rect.top <= win.innerHeight && rect.left <= win.innerWidth;
  };
  const visibleText = (candidate) => {
    if (!candidate) return "";
    const children = Array.from(candidate.children || []).filter(isVisible);
    if (children.length === 0) return normalizeText(candidate.innerText || candidate.textContent || "");
    const ownText = normalizeText(
      Array.from(candidate.childNodes || [])
        .filter((node) => node.nodeType === 3)
        .map((node) => node.textContent || "")
        .join(" ")
    );
    const childText = normalizeText(children.map((child) => visibleText(child)).join(" "));
    return normalizeText([ownText, childText].filter(Boolean).join(" "));
  };
  const textOf = (candidate, limit = 240) => truncate(visibleText(candidate), limit) || null;
  const boolAttribute = (candidate, name) => {
    const value = candidate.getAttribute(name);
    if (value === null) return null;
    if (value === "true" || value === "") return true;
    if (value === "false") return false;
    return null;
  };
  const inputTypeOf = (candidate) => {
    const tag = candidate.tagName.toLowerCase();
    if (tag === "button") return normalizeText(candidate.getAttribute("type")).toLowerCase() || (candidate.form ? "submit" : "button");
    if (tag === "input") return normalizeText(candidate.getAttribute("type")).toLowerCase() || "text";
    if (tag === "textarea") return "textarea";
    if (tag === "select") return candidate.multiple ? "select-multiple" : "select";
    if (candidate.isContentEditable) return "contenteditable";
    return null;
  };
  const isField = (candidate) => {
    const tag = candidate.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || candidate.isContentEditable;
  };
  const labelsOf = (candidate) => {
    try {
      if (candidate.labels && candidate.labels.length > 0) {
        return normalizeText(Array.from(candidate.labels).map((label) => label.innerText || label.textContent).join(" "));
      }
    } catch {
      return "";
    }
    return "";
  };
  const labelledByText = (candidate) => {
    const ids = normalizeText(candidate.getAttribute("aria-labelledby"));
    if (!ids) return "";
    return normalizeText(ids.split(" ").map((id) => {
      const label = doc.getElementById(id);
      return label ? label.innerText || label.textContent || "" : "";
    }).join(" "));
  };
  const nameOf = (candidate) => {
    const aria = normalizeText(candidate.getAttribute("aria-label"));
    if (aria) return aria;
    const labelled = labelledByText(candidate);
    if (labelled) return labelled;
    const labels = labelsOf(candidate);
    if (labels) return labels;
    const alt = normalizeText(candidate.getAttribute("alt"));
    if (alt) return alt;
    const title = normalizeText(candidate.getAttribute("title"));
    if (title) return title;
    const placeholder = normalizeText(candidate.getAttribute("placeholder"));
    if (placeholder) return placeholder;
    const type = inputTypeOf(candidate);
    if (["button", "submit", "reset"].includes(type || "")) {
      const buttonValue = normalizeText(candidate.getAttribute("value"));
      if (buttonValue) return buttonValue;
    }
    return textOf(candidate, 160);
  };
  const roleOf = (candidate) => {
    const explicit = normalizeText(candidate.getAttribute("role"));
    if (explicit) return explicit.toLowerCase();
    const tag = candidate.tagName.toLowerCase();
    const type = normalizeText(candidate.getAttribute("type")).toLowerCase();
    if (tag === "a" && candidate.hasAttribute("href")) return "link";
    if (tag === "button" || ["button", "submit", "reset"].includes(type)) return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "input") {
      if (["checkbox"].includes(type)) return "checkbox";
      if (["radio"].includes(type)) return "radio";
      if (["range"].includes(type)) return "slider";
      return "textbox";
    }
    if (candidate.isContentEditable) return "textbox";
    return tag;
  };
  const visibleTextOfInteractive = (candidate) => {
    if (isField(candidate)) {
      const type = inputTypeOf(candidate);
      if (!["button", "submit", "reset"].includes(type || "")) return null;
    }
    return textOf(candidate, 240);
  };
  const stateOf = (candidate) => ({
    disabled: Boolean(candidate.disabled || candidate.getAttribute("aria-disabled") === "true"),
    checked: typeof candidate.checked === "boolean" ? Boolean(candidate.checked) : boolAttribute(candidate, "aria-checked"),
    expanded: boolAttribute(candidate, "aria-expanded"),
    pressed: boolAttribute(candidate, "aria-pressed"),
    selected: typeof candidate.selected === "boolean" ? Boolean(candidate.selected) : boolAttribute(candidate, "aria-selected"),
    required: Boolean(candidate.required || candidate.getAttribute("aria-required") === "true"),
    readonly: Boolean(candidate.readOnly || candidate.getAttribute("aria-readonly") === "true"),
  });
  return {
    isVisible: isVisible(element),
    isField: isField(element),
    role: roleOf(element),
    accessibleName: nameOf(element),
    visibleText: visibleTextOfInteractive(element),
    state: stateOf(element),
    targetUrl: element.href || null,
    inputType: inputTypeOf(element),
    fieldName: normalizeText(element.getAttribute("name")) || null,
    placeholder: normalizeText(element.getAttribute("placeholder")) || null,
  };
}
"""
