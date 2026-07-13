"""Semantic Observation Engine implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scout_pilot.browser.engine import BrowserEngine
from scout_pilot.browser.types import (
    BrowserDialogSnapshot,
    BrowserElementLocation,
    BrowserElementState,
    BrowserFocusedElementSnapshot,
    BrowserFormFieldSnapshot,
    BrowserInteractiveElementSnapshot,
    BrowserPageSnapshot,
    BrowserSectionSnapshot,
)
from scout_pilot.models import (
    DialogSummary,
    ElementLocation,
    ElementState,
    FocusedElementSummary,
    FormFieldSummary,
    InteractiveElement,
    PageIssue,
    PageIssueCode,
    PageMetadata,
    PageObservation,
    SemanticElement,
    SemanticSection,
)
from scout_pilot.semantic_ids import (
    semantic_dedupe_key,
    stable_semantic_id,
    truncate_optional_semantic_text,
    truncate_semantic_text,
)

if TYPE_CHECKING:
    from scout_pilot.config import AppConfig


_COOKIE_PATTERN = re.compile(
    r"\b(cookie|cookies|consent|privacy settings|accept all|reject all|"
    r"файл(?:ы)? cookie|куки|согласие|конфиденциальност)\b",
    re.IGNORECASE,
)
_LOGIN_PATTERN = re.compile(
    r"\b(sign in|log in|login required|authentication required|create account|"
    r"войдите|войти|авторизуйтесь|требуется вход|зарегистрируйтесь)\b",
    re.IGNORECASE,
)
_CAPTCHA_PATTERN = re.compile(
    r"\b(captcha|recaptcha|hcaptcha|verify you are human|prove you are human|"
    r"unusual traffic|robot|automated requests|провер(?:ьте|ка),? что вы человек|"
    r"капч|робот|автоматическ(?:ие|их) запрос)\b",
    re.IGNORECASE,
)
_REGION_PATTERN = re.compile(
    r"\b(select (?:your )?(?:region|location|city|country)|choose (?:your )?(?:region|location|city|country)|"
    r"allow location|use your location|geolocation|"
    r"выберите (?:регион|город|страну|локацию)|определить (?:местоположение|город)|"
    r"разрешить доступ к местоположению)\b",
    re.IGNORECASE,
)
_BLOCKED_PATTERN = re.compile(
    r"\b(access denied|forbidden|blocked|temporarily unavailable|enable javascript|"
    r"доступ запрещен|доступ ограничен|заблокировано|включите javascript)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ObservationSettings:
    """Limits applied before observations are sent to an LLM."""

    max_sections: int = 12
    max_interactive_elements: int = 60
    max_form_fields: int = 25
    max_dialogs: int = 5
    max_section_chars: int = 700
    max_total_chars: int = 12000

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "ObservationSettings":
        return cls(
            max_sections=config.observation_max_sections,
            max_interactive_elements=config.observation_max_interactive_elements,
            max_form_fields=config.observation_max_form_fields,
            max_dialogs=config.observation_max_dialogs,
            max_section_chars=config.observation_max_section_chars,
            max_total_chars=config.observation_max_total_chars,
        )

    def as_limits(self) -> dict[str, int]:
        return {
            "max_sections": self.max_sections,
            "max_interactive_elements": self.max_interactive_elements,
            "max_form_fields": self.max_form_fields,
            "max_dialogs": self.max_dialogs,
            "max_section_chars": self.max_section_chars,
            "max_total_chars": self.max_total_chars,
        }

    def __post_init__(self) -> None:
        for name, value in self.as_limits().items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")


class SemanticObservationEngine:
    """Build compact LLM-safe observations from Browser Engine snapshots."""

    def __init__(
        self,
        browser: BrowserEngine,
        settings: ObservationSettings | None = None,
    ) -> None:
        self._browser = browser
        self._settings = settings or ObservationSettings()

    async def observe(self) -> PageObservation:
        snapshot = await self._browser.capture_semantic_snapshot()
        sections, sections_truncated = self._build_sections(snapshot.sections)
        elements, elements_truncated = self._build_interactive_elements(
            snapshot.interactive_elements
        )
        fields, fields_truncated = self._build_form_fields(snapshot.form_fields)
        dialogs, dialogs_truncated = self._build_dialogs(snapshot.dialogs)
        focused_element = _build_focused_element(snapshot.focused_element)
        issues = self._build_issues(
            snapshot,
            any((sections_truncated, elements_truncated, fields_truncated, dialogs_truncated)),
        )

        observation = PageObservation(
            url=snapshot.url,
            title=snapshot.title,
            summary=_build_summary(snapshot, sections, elements, fields, dialogs, issues),
            metadata=PageMetadata(
                url=snapshot.url,
                title=snapshot.title,
                origin=snapshot.origin,
                load_state=snapshot.load_state,
                is_visible=snapshot.is_visible,
                viewport_width=snapshot.viewport_width,
                viewport_height=snapshot.viewport_height,
            ),
            sections=sections,
            interactive_elements=elements,
            form_fields=fields,
            focused_element=focused_element,
            dialogs=dialogs,
            issues=issues,
            limits=self._settings.as_limits(),
            elements=[
                SemanticElement(
                    role=element.role,
                    label=element.accessible_name or element.visible_text,
                    index=index,
                    is_interactive=True,
                )
                for index, element in enumerate(elements)
            ],
        )
        return self._fit_total_size(observation)

    def _build_sections(
        self,
        snapshots: tuple[BrowserSectionSnapshot, ...],
    ) -> tuple[tuple[SemanticSection, ...], bool]:
        sections: list[SemanticSection] = []
        seen: set[str] = set()
        truncated = len(snapshots) > self._settings.max_sections

        prioritized = sorted(
            enumerate(snapshots),
            key=lambda item: (-_browser_section_priority(item[1]), item[0]),
        )
        for _, snapshot in prioritized:
            text = _truncate(snapshot.text, self._settings.max_section_chars)
            key = _dedupe_key(snapshot.role, snapshot.heading, text)
            if not text or key in seen:
                continue
            seen.add(key)
            sections.append(
                SemanticSection(
                    section_id=_stable_id("sec", snapshot.role, snapshot.heading, text),
                    role=snapshot.role,
                    heading=snapshot.heading,
                    text=text,
                    location=_location(snapshot.location),
                )
            )
            if len(sections) >= self._settings.max_sections:
                truncated = True
                break

        return tuple(sections), truncated

    def _build_interactive_elements(
        self,
        snapshots: tuple[BrowserInteractiveElementSnapshot, ...],
    ) -> tuple[tuple[InteractiveElement, ...], bool]:
        elements: list[InteractiveElement] = []
        seen: set[str] = set()
        truncated = len(snapshots) > self._settings.max_interactive_elements

        prioritized = sorted(
            enumerate(snapshots),
            key=lambda item: (-_browser_interactive_priority(item[1]), item[0]),
        )
        for _, snapshot in prioritized:
            name = _truncate_optional(snapshot.accessible_name, 160)
            text = _truncate_optional(snapshot.visible_text, 160)
            key = _dedupe_key(snapshot.role, name, text, snapshot.target_url, snapshot.input_type)
            if key in seen:
                continue
            seen.add(key)
            elements.append(
                InteractiveElement(
                    element_id=_stable_id(
                        "el",
                        snapshot.role,
                        name,
                        text,
                        snapshot.target_url,
                        snapshot.input_type,
                    ),
                    role=snapshot.role,
                    accessible_name=name,
                    visible_text=text,
                    state=_state(snapshot.state),
                    location=_location(snapshot.location),
                    target_url=snapshot.target_url,
                    input_type=snapshot.input_type,
                )
            )
            if len(elements) >= self._settings.max_interactive_elements:
                truncated = True
                break

        return tuple(elements), truncated

    def _build_form_fields(
        self,
        snapshots: tuple[BrowserFormFieldSnapshot, ...],
    ) -> tuple[tuple[FormFieldSummary, ...], bool]:
        fields: list[FormFieldSummary] = []
        seen: set[str] = set()
        truncated = len(snapshots) > self._settings.max_form_fields

        for snapshot in snapshots:
            label = _truncate_optional(snapshot.label, 160)
            placeholder = _truncate_optional(snapshot.placeholder, 160)
            key = _dedupe_key(
                snapshot.role,
                snapshot.input_type,
                label,
                placeholder,
                snapshot.field_name,
            )
            if key in seen:
                continue
            seen.add(key)
            fields.append(
                FormFieldSummary(
                    field_id=_stable_id(
                        "field",
                        snapshot.role,
                        snapshot.input_type,
                        label,
                        placeholder,
                        snapshot.field_name,
                    ),
                    role=snapshot.role,
                    input_type=snapshot.input_type,
                    label=label,
                    placeholder=placeholder,
                    value_state=snapshot.value_state,
                    state=_state(snapshot.state),
                    location=_location(snapshot.location),
                    field_name=snapshot.field_name,
                )
            )
            if len(fields) >= self._settings.max_form_fields:
                truncated = True
                break

        return tuple(fields), truncated

    def _build_dialogs(
        self,
        snapshots: tuple[BrowserDialogSnapshot, ...],
    ) -> tuple[tuple[DialogSummary, ...], bool]:
        dialogs: list[DialogSummary] = []
        seen: set[str] = set()
        truncated = len(snapshots) > self._settings.max_dialogs

        for snapshot in snapshots:
            text = _truncate(snapshot.text, self._settings.max_section_chars)
            key = _dedupe_key(snapshot.role, snapshot.title, text)
            if not text or key in seen:
                continue
            seen.add(key)
            dialogs.append(
                DialogSummary(
                    dialog_id=_stable_id("dialog", snapshot.role, snapshot.title, text),
                    role=snapshot.role,
                    title=_truncate_optional(snapshot.title, 160),
                    text=text,
                    location=_location(snapshot.location),
                )
            )
            if len(dialogs) >= self._settings.max_dialogs:
                truncated = True
                break

        return tuple(dialogs), truncated

    def _build_issues(
        self,
        snapshot: BrowserPageSnapshot,
        truncated: bool,
    ) -> tuple[PageIssue, ...]:
        issues: list[PageIssue] = []
        for code in snapshot.issues:
            issue = _issue_from_browser_code(code)
            if issue is not None:
                issues.append(issue)

        issues.extend(_classify_blocker_issues(snapshot))

        if not snapshot.sections and not snapshot.interactive_elements and "empty_page" not in snapshot.issues:
            issues.append(
                PageIssue(
                    code=PageIssueCode.EMPTY_PAGE,
                    message="No visible semantic content was detected.",
                )
            )
        if truncated:
            issues.append(
                PageIssue(
                    code=PageIssueCode.OBSERVATION_TRUNCATED,
                    message="Observation was truncated to fit configured limits.",
                    severity="warning",
                )
            )

        return tuple(_dedupe_issues(issues))

    def _fit_total_size(self, observation: PageObservation) -> PageObservation:
        if len(str(observation.to_llm_context())) <= self._settings.max_total_chars:
            return observation

        sections = list(observation.sections)
        elements = list(observation.interactive_elements)
        fields = list(observation.form_fields)
        dialogs = list(observation.dialogs)
        issues = tuple(
            _dedupe_issues(
                [
                    *observation.issues,
                    PageIssue(
                        code=PageIssueCode.OBSERVATION_TRUNCATED,
                        message="Observation was compressed to fit the total size limit.",
                        severity="warning",
                    ),
                ]
            )
        )
        fitted = _copy_observation_with(
            observation,
            sections=sections,
            elements=elements,
            fields=fields,
            dialogs=dialogs,
            issues=issues,
        )

        while len(str(fitted.to_llm_context())) > self._settings.max_total_chars:
            if len(elements) > 12:
                elements.pop()
            elif len(fields) > 4:
                fields.pop()
            elif len(dialogs) > 1:
                dialogs.pop()
            elif len(elements) > 8:
                elements.pop()
            elif len(fields) > 2:
                fields.pop()
            elif len(sections) > 8:
                sections.pop()
            elif elements:
                elements.pop()
            elif fields:
                fields.pop()
            elif dialogs:
                dialogs.pop()
            elif sections:
                sections.pop()
            else:
                break
            fitted = _copy_observation_with(
                observation,
                sections=sections,
                elements=elements,
                fields=fields,
                dialogs=dialogs,
                issues=issues,
            )
        return fitted


def _build_focused_element(
    snapshot: BrowserFocusedElementSnapshot | None,
) -> FocusedElementSummary | None:
    if snapshot is None:
        return None
    return FocusedElementSummary(
        role=snapshot.role,
        accessible_name=_truncate_optional(snapshot.accessible_name, 160),
        visible_text=_truncate_optional(snapshot.visible_text, 160),
        input_type=snapshot.input_type,
        value_state=snapshot.value_state,
    )


def _browser_section_priority(snapshot: BrowserSectionSnapshot) -> int:
    role = snapshot.role.casefold()
    text = f"{snapshot.heading or ''} {snapshot.text[:240]}".casefold()
    score = {
        "main": 120,
        "article": 110,
        "region": 90,
        "search": 90,
        "form": 80,
        "aside": 50,
        "banner": 20,
        "navigation": 10,
        "contentinfo": 5,
    }.get(role, 40)
    if any(term in text for term in ("result", "search", "результат", "поиск")):
        score += 20
    if any(
        term in text
        for term in (
            "requirements",
            "qualifications",
            "responsibilities",
            "skills",
            "experience",
            "требован",
            "квалификац",
            "обязанност",
            "навык",
            "опыт",
        )
    ):
        score += 30
    return score


def _browser_interactive_priority(snapshot: BrowserInteractiveElementSnapshot) -> int:
    role = snapshot.role.casefold()
    name = (snapshot.accessible_name or snapshot.visible_text or "").strip()
    score = 0
    if snapshot.state.disabled:
        score -= 100
    if role == "link" and snapshot.target_url:
        score += 45
        score += min(snapshot.target_url.count("/"), 8)
    elif role in {"button", "menuitem", "tab"}:
        score += 35
    elif role in {"searchbox", "textbox", "combobox"}:
        score += 30
    if snapshot.input_type in {"search", "submit"}:
        score += 20
    if name:
        score += min(len(name) // 8, 20)
        if len(name.split()) >= 3:
            score += 10
    return score


def _build_summary(
    snapshot: BrowserPageSnapshot,
    sections: tuple[SemanticSection, ...],
    elements: tuple[InteractiveElement, ...],
    fields: tuple[FormFieldSummary, ...],
    dialogs: tuple[DialogSummary, ...],
    issues: tuple[PageIssue, ...],
) -> str:
    title = snapshot.title or "Untitled page"
    issue_codes = ", ".join(issue.code.value for issue in issues) or "none"
    return (
        f"{title}. Sections: {len(sections)}. Interactive elements: {len(elements)}. "
        f"Form fields: {len(fields)}. Dialogs: {len(dialogs)}. Issues: {issue_codes}."
    )


def _issue_from_browser_code(code: str) -> PageIssue | None:
    mapping = {
        "loading": PageIssue(PageIssueCode.LOADING, "Page is still loading."),
        "empty_page": PageIssue(PageIssueCode.EMPTY_PAGE, "Page appears empty."),
        "blocked_page": PageIssue(
            PageIssueCode.BLOCKED_PAGE,
            "Page text suggests the user may be blocked or challenged.",
            severity="warning",
        ),
        "navigation_error": PageIssue(
            PageIssueCode.NAVIGATION_ERROR,
            "The last navigation failed.",
            severity="warning",
        ),
        "navigation_timeout": PageIssue(
            PageIssueCode.NAVIGATION_ERROR,
            "The last navigation timed out.",
            severity="warning",
        ),
        "invalid_url": PageIssue(
            PageIssueCode.NAVIGATION_ERROR,
            "The requested URL was invalid.",
            severity="warning",
        ),
        "browser_not_started": PageIssue(
            PageIssueCode.OBSERVATION_ERROR,
            "The browser is not started.",
            severity="warning",
        ),
        "observation_error": PageIssue(
            PageIssueCode.OBSERVATION_ERROR,
            "The page could not be observed completely.",
            severity="warning",
        ),
    }
    mapped = mapping.get(code)
    if mapped is not None:
        return mapped
    if code.endswith("_timeout") or code.endswith("_error"):
        return PageIssue(
            PageIssueCode.NAVIGATION_ERROR,
            "The last browser navigation action failed.",
            severity="warning",
        )
    return None


def _classify_blocker_issues(snapshot: BrowserPageSnapshot) -> list[PageIssue]:
    text = _snapshot_visible_text(snapshot)
    dialog_text = " ".join(
        f"{dialog.title or ''} {dialog.text}" for dialog in snapshot.dialogs
    )
    issues: list[PageIssue] = []
    if snapshot.dialogs:
        issues.append(
            PageIssue(
                PageIssueCode.MODAL_DIALOG,
                "A visible modal or dialog is present.",
                severity="warning",
            )
        )
    if _COOKIE_PATTERN.search(text):
        issues.append(
            PageIssue(
                PageIssueCode.COOKIE_BANNER,
                "Visible text looks like a cookie or consent banner.",
                severity="warning",
            )
        )
    if _LOGIN_PATTERN.search(text) or _has_password_field(snapshot):
        issues.append(
            PageIssue(
                PageIssueCode.LOGIN_WALL,
                "The page appears to require a manual login or account access.",
                severity="warning",
            )
        )
    if _CAPTCHA_PATTERN.search(text):
        issues.append(
            PageIssue(
                PageIssueCode.CAPTCHA_BLOCKING_PAGE,
                "The page appears to require a CAPTCHA or human verification.",
                severity="warning",
            )
        )
    if snapshot.dialogs and _REGION_PATTERN.search(dialog_text):
        issues.append(
            PageIssue(
                PageIssueCode.REGION_PROMPT,
                "The page appears to ask for region, city or location selection.",
                severity="warning",
            )
        )
    if _BLOCKED_PATTERN.search(text):
        issues.append(
            PageIssue(
                PageIssueCode.BLOCKED_PAGE,
                "Page text suggests the user may be blocked or challenged.",
                severity="warning",
            )
        )
    return issues


def _snapshot_visible_text(snapshot: BrowserPageSnapshot) -> str:
    parts: list[str] = []
    for section in snapshot.sections:
        parts.extend((section.role, section.heading or "", section.text))
    for element in snapshot.interactive_elements:
        parts.extend(
            (
                element.role,
                element.accessible_name or "",
                element.visible_text or "",
                element.input_type or "",
            )
        )
    for field in snapshot.form_fields:
        parts.extend(
            (
                field.role,
                field.input_type or "",
                field.label or "",
                field.placeholder or "",
                field.field_name or "",
            )
        )
    for dialog in snapshot.dialogs:
        parts.extend((dialog.role, dialog.title or "", dialog.text))
    if snapshot.title:
        parts.append(snapshot.title)
    return " ".join(parts)


def _has_password_field(snapshot: BrowserPageSnapshot) -> bool:
    return any(field.input_type == "password" for field in snapshot.form_fields)


def _copy_observation_with(
    observation: PageObservation,
    sections: list[SemanticSection],
    elements: list[InteractiveElement],
    fields: list[FormFieldSummary],
    dialogs: list[DialogSummary],
    issues: tuple[PageIssue, ...],
) -> PageObservation:
    return PageObservation(
        url=observation.url,
        title=observation.title,
        summary=observation.summary,
        metadata=observation.metadata,
        sections=sections,
        interactive_elements=elements,
        form_fields=fields,
        focused_element=observation.focused_element,
        dialogs=dialogs,
        issues=issues,
        limits=observation.limits,
        elements=[
            SemanticElement(
                role=element.role,
                label=element.accessible_name or element.visible_text,
                index=index,
                is_interactive=True,
            )
            for index, element in enumerate(elements)
        ],
    )


def _dedupe_issues(issues: list[PageIssue]) -> list[PageIssue]:
    result: list[PageIssue] = []
    seen: set[PageIssueCode] = set()
    for issue in issues:
        if issue.code in seen:
            continue
        seen.add(issue.code)
        result.append(issue)
    return result


def _location(location: BrowserElementLocation | None) -> ElementLocation | None:
    if location is None:
        return None
    return ElementLocation(
        region=location.region,
        x_ratio=location.x_ratio,
        y_ratio=location.y_ratio,
        width_ratio=location.width_ratio,
        height_ratio=location.height_ratio,
    )


def _state(state: BrowserElementState) -> ElementState:
    return ElementState(
        disabled=state.disabled,
        checked=state.checked,
        expanded=state.expanded,
        pressed=state.pressed,
        selected=state.selected,
        required=state.required,
        readonly=state.readonly,
    )


def _stable_id(prefix: str, *parts: object) -> str:
    return stable_semantic_id(prefix, *parts)


def _dedupe_key(*parts: object) -> str:
    return semantic_dedupe_key(*parts)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _truncate(text: str, limit: int) -> str:
    return truncate_semantic_text(text, limit)


def _truncate_optional(text: str | None, limit: int) -> str | None:
    return truncate_optional_semantic_text(text, limit)
