"""Deterministic resolver for website-neutral semantic navigation intents."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from scout_pilot.models import (
    ElementLocation,
    FormFieldSummary,
    InteractiveElement,
    PageObservation,
    SemanticSection,
)
from scout_pilot.navigation.types import (
    FormFillPlan,
    FormFillPlanStep,
    NavigationIntent,
    NavigationIntentKind,
    PageTransition,
    SemanticCandidate,
    SemanticResolution,
    SemanticResolutionStatus,
)


_WORD_PATTERN = re.compile(r"[a-z0-9а-яё]+", re.IGNORECASE)
_STABLE_RESOURCE_ID_PATTERN = re.compile(
    r"(?:\d{3,}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_SEARCH_TERMS = frozenset(
    {
        "search",
        "find",
        "lookup",
        "query",
        "искать",
        "поиск",
        "найти",
        "искать",
        "запрос",
    }
)
_CLICKABLE_ROLES = frozenset(
    {
        "button",
        "link",
        "menuitem",
        "tab",
        "checkbox",
        "radio",
        "summary",
    }
)
_FIELD_ROLES = frozenset({"textbox", "combobox", "searchbox"})
_DEFAULT_MIN_SCORE = 35
_AMBIGUITY_MARGIN = 8
_REMAP_MIN_SCORE = 65
_MAIN_CONTENT_ROLES = frozenset({"main", "article", "region", "section", "form", "search"})
_LOCAL_CONTEXT_ROLES = frozenset({"article", "region", "section", "form", "search", "dialog"})


@dataclass(frozen=True)
class SemanticNavigationResolver:
    """Resolve semantic intents against compact observations."""

    min_score: int = _DEFAULT_MIN_SCORE
    ambiguity_margin: int = _AMBIGUITY_MARGIN

    def resolve(
        self,
        observation: PageObservation,
        intent: NavigationIntent,
    ) -> SemanticResolution:
        if intent.kind is NavigationIntentKind.SEARCH_FIELD:
            return self.resolve_search_field(observation, intent.context)
        if intent.kind is NavigationIntentKind.FIELD:
            if not intent.target:
                return _invalid(intent, "Field intent requires a target label.")
            return self.resolve_form_field(observation, intent.target, intent.context)
        if intent.kind is NavigationIntentKind.CLICK:
            if not intent.target:
                return _invalid(intent, "Click intent requires target text.")
            return self.resolve_click(
                observation,
                target=intent.target,
                role=intent.role,
                context=intent.context,
            )
        if intent.kind is NavigationIntentKind.NAVIGATE:
            return _invalid(intent, "Navigation URL selection is handled by browser.navigate.")
        return _invalid(intent, "Unsupported navigation intent.")

    def resolve_click(
        self,
        observation: PageObservation,
        *,
        target: str,
        role: str | None = None,
        context: str | None = None,
    ) -> SemanticResolution:
        intent = NavigationIntent(
            NavigationIntentKind.CLICK,
            target=target,
            role=role,
            context=context,
        )
        requested_role = _normalize_role(role)
        candidates = [
            _candidate_from_interactive(element, observation)
            for element in observation.interactive_elements
            if _is_click_candidate(element, requested_role)
        ]
        scored = _score_candidates(
            candidates,
            target=target,
            role=requested_role,
            context=context,
            min_score=self.min_score,
        )
        return _resolution_from_scored(
            intent,
            scored,
            ambiguity_margin=self.ambiguity_margin,
            not_found_message="No visible interactive element matched the semantic intent.",
        )

    def resolve_form_field(
        self,
        observation: PageObservation,
        target: str,
        context: str | None = None,
    ) -> SemanticResolution:
        if _is_search_intent(target):
            search = self.resolve_search_field(observation, context)
            if search.is_resolved:
                return SemanticResolution(
                    status=search.status,
                    intent=NavigationIntent(NavigationIntentKind.FIELD, target, context=context),
                    selected=search.selected,
                    candidates=search.candidates,
                    message=search.message,
                )

        intent = NavigationIntent(NavigationIntentKind.FIELD, target=target, context=context)
        candidates = [
            _candidate_from_field(field, observation)
            for field in observation.form_fields
            if _is_fillable_field(field)
        ]
        scored = _score_candidates(
            candidates,
            target=target,
            context=context,
            min_score=self.min_score,
        )
        return _resolution_from_scored(
            intent,
            scored,
            ambiguity_margin=self.ambiguity_margin,
            not_found_message="No visible form field matched the semantic label.",
        )

    def resolve_search_field(
        self,
        observation: PageObservation,
        context: str | None = None,
    ) -> SemanticResolution:
        intent = NavigationIntent(NavigationIntentKind.SEARCH_FIELD, context=context)
        candidates = [
            _candidate_from_field(field, observation)
            for field in observation.form_fields
            if _is_fillable_field(field)
        ]
        scored: list[SemanticCandidate] = []
        for candidate in candidates:
            score, reasons = _score_search_field(candidate, context)
            if score >= 1:
                scored.append(_with_score(candidate, score, reasons))

        if not scored and len(candidates) == 1:
            only = candidates[0]
            if only.role in _FIELD_ROLES or only.input_type in {"text", "search", "textarea"}:
                scored.append(_with_score(only, 40, ("single_visible_field",)))

        return _resolution_from_scored(
            intent,
            sorted(scored, key=lambda item: item.score, reverse=True),
            ambiguity_margin=self.ambiguity_margin,
            not_found_message="No generic search field was detected.",
        )

    def plan_form_fill(
        self,
        observation: PageObservation,
        requested_labels: Sequence[str],
    ) -> FormFillPlan:
        steps: list[FormFillPlanStep] = []
        for label in requested_labels:
            resolution = self.resolve_form_field(observation, label)
            selected = resolution.selected
            steps.append(
                FormFillPlanStep(
                    requested_label=label,
                    status=resolution.status,
                    field_id=selected.element_id if selected else None,
                    field_label=selected.name if selected else None,
                    message=resolution.message,
                    candidates=resolution.candidates,
                )
            )
        return FormFillPlan(tuple(steps))

    def remap_click_candidate(
        self,
        before: PageObservation,
        after: PageObservation,
        element_id: str,
    ) -> SemanticResolution:
        """Remap a stale interactive observation ID to a fresh semantic candidate."""

        source = _interactive_candidate_by_id(before, element_id)
        if source is None:
            return SemanticResolution(
                status=SemanticResolutionStatus.NOT_FOUND,
                intent=NavigationIntent(NavigationIntentKind.CLICK),
                message="Original semantic element ID was not present in the previous observation.",
            )
        candidates = [
            _candidate_from_interactive(element, after)
            for element in after.interactive_elements
            if _is_click_candidate(element, source.role)
        ]
        return _remap_resolution_from_candidates(
            source,
            candidates,
            NavigationIntent(
                NavigationIntentKind.CLICK,
                target=source.name or source.visible_text,
                role=source.role,
                context=source.context,
            ),
            ambiguity_margin=self.ambiguity_margin,
        )

    def remap_field_candidate(
        self,
        before: PageObservation,
        after: PageObservation,
        field_id: str,
    ) -> SemanticResolution:
        """Remap a stale form field observation ID to a fresh semantic field."""

        source = _field_candidate_by_id(before, field_id)
        if source is None:
            return SemanticResolution(
                status=SemanticResolutionStatus.NOT_FOUND,
                intent=NavigationIntent(NavigationIntentKind.FIELD),
                message="Original semantic field ID was not present in the previous observation.",
            )
        candidates = [
            _candidate_from_field(field, after)
            for field in after.form_fields
            if _is_fillable_field(field)
        ]
        return _remap_resolution_from_candidates(
            source,
            candidates,
            NavigationIntent(
                NavigationIntentKind.FIELD,
                target=source.name or source.visible_text,
                role=source.role,
                context=source.context,
            ),
            ambiguity_margin=self.ambiguity_margin,
        )

    def detect_transition(
        self,
        before: PageObservation | None,
        after: PageObservation | None,
    ) -> PageTransition:
        return detect_page_transition(before, after)


def detect_page_transition(
    before: PageObservation | None,
    after: PageObservation | None,
) -> PageTransition:
    """Detect whether a page changed after an action without reading raw DOM."""

    if before is None or after is None:
        return PageTransition(changed=False, reason="observation_missing")
    before_load_state = before.metadata.load_state if before.metadata else None
    after_load_state = after.metadata.load_state if after.metadata else None
    if before.url != after.url:
        return PageTransition(
            changed=True,
            reason="url_changed",
            before_url=before.url,
            after_url=after.url,
            before_title=before.title,
            after_title=after.title,
            before_load_state=before_load_state,
            after_load_state=after_load_state,
        )
    if before.title != after.title:
        return PageTransition(
            changed=True,
            reason="title_changed",
            before_url=before.url,
            after_url=after.url,
            before_title=before.title,
            after_title=after.title,
            before_load_state=before_load_state,
            after_load_state=after_load_state,
        )
    if before_load_state != after_load_state:
        return PageTransition(
            changed=True,
            reason="load_state_changed",
            before_url=before.url,
            after_url=after.url,
            before_title=before.title,
            after_title=after.title,
            before_load_state=before_load_state,
            after_load_state=after_load_state,
        )
    if _main_content_signature(before) != _main_content_signature(after):
        return PageTransition(
            changed=True,
            reason="main_content_changed",
            before_url=before.url,
            after_url=after.url,
            before_title=before.title,
            after_title=after.title,
            before_load_state=before_load_state,
            after_load_state=after_load_state,
        )
    if _observation_signature(before) != _observation_signature(after):
        return PageTransition(
            changed=True,
            reason="semantic_state_changed",
            before_url=before.url,
            after_url=after.url,
            before_title=before.title,
            after_title=after.title,
            before_load_state=before_load_state,
            after_load_state=after_load_state,
        )
    return PageTransition(
        changed=False,
        reason="repeated_observation",
        before_url=before.url,
        after_url=after.url,
        before_title=before.title,
        after_title=after.title,
        before_load_state=before_load_state,
        after_load_state=after_load_state,
        repeated=True,
    )


def _resolution_from_scored(
    intent: NavigationIntent,
    scored: Sequence[SemanticCandidate],
    *,
    ambiguity_margin: int,
    not_found_message: str,
) -> SemanticResolution:
    if not scored:
        return SemanticResolution(
            status=SemanticResolutionStatus.NOT_FOUND,
            intent=intent,
            message=not_found_message,
        )

    ordered = tuple(
        _coalesce_equivalent_link_destinations(
            sorted(scored, key=lambda item: item.score, reverse=True)
        )
    )
    best = ordered[0]
    if len(ordered) > 1 and best.score - ordered[1].score < ambiguity_margin:
        contenders = tuple(
            candidate
            for candidate in ordered
            if best.score - candidate.score < ambiguity_margin
        )
        if _can_select_contextual_read_only_link(intent, contenders):
            return SemanticResolution(
                status=SemanticResolutionStatus.RESOLVED,
                intent=intent,
                selected=best,
                candidates=ordered[:5],
                message="Contextual read-only link target resolved deterministically.",
            )
        return SemanticResolution(
            status=SemanticResolutionStatus.AMBIGUOUS,
            intent=intent,
            candidates=ordered[:5],
            message="Multiple visible semantic candidates matched the intent.",
        )
    return SemanticResolution(
        status=SemanticResolutionStatus.RESOLVED,
        intent=intent,
        selected=best,
        candidates=ordered[:5],
        message="Semantic target resolved.",
    )


def _coalesce_equivalent_link_destinations(
    candidates: Sequence[SemanticCandidate],
) -> tuple[SemanticCandidate, ...]:
    """Treat duplicate rendered links to the same destination as one target."""

    unique: list[SemanticCandidate] = []
    seen_destinations: set[tuple[str, str]] = set()
    for candidate in candidates:
        destination = _link_destination_identity(candidate)
        if destination is not None:
            if destination in seen_destinations:
                continue
            seen_destinations.add(destination)
        unique.append(candidate)
    return tuple(unique)


def _link_destination_identity(candidate: SemanticCandidate) -> tuple[str, str] | None:
    if candidate.role != "link" or not candidate.target_url:
        return None
    parsed = urlsplit(candidate.target_url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    query = "" if _STABLE_RESOURCE_ID_PATTERN.search(parsed.path) else parsed.query
    destination = urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path or "/",
            query,
            "",
        )
    )
    return candidate.role, destination


def _can_select_contextual_read_only_link(
    intent: NavigationIntent,
    contenders: Sequence[SemanticCandidate],
) -> bool:
    if intent.role != "link" or len(_tokens(intent.context or "")) < 2:
        return False
    if not contenders:
        return False
    if not all(_is_public_link_candidate(candidate) for candidate in contenders):
        return False
    best = contenders[0]
    target_matched = {
        "exact_name_match",
        "all_target_terms_match",
    }.intersection(best.reasons)
    return bool(target_matched and "context_terms_match" in best.reasons)


def _is_public_link_candidate(candidate: SemanticCandidate) -> bool:
    if candidate.role != "link" or not candidate.target_url:
        return False
    parsed = urlsplit(candidate.target_url.strip())
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def _score_candidates(
    candidates: Iterable[SemanticCandidate],
    *,
    target: str,
    role: str | None = None,
    context: str | None = None,
    min_score: int,
) -> list[SemanticCandidate]:
    target_tokens = _expanded_tokens(target)
    context_tokens = _tokens(context or "")
    scored: list[SemanticCandidate] = []
    for candidate in candidates:
        score, reasons = _score_candidate(candidate, target_tokens, role, context_tokens)
        if score >= min_score:
            scored.append(_with_score(candidate, score, reasons))
    return sorted(scored, key=lambda item: item.score, reverse=True)


def _score_candidate(
    candidate: SemanticCandidate,
    target_tokens: frozenset[str],
    role: str | None,
    context_tokens: frozenset[str],
) -> tuple[int, tuple[str, ...]]:
    text = _candidate_text(candidate)
    text_tokens = _tokens(text)
    reasons: list[str] = []
    score = 0

    if candidate.disabled:
        return 0, ("disabled",)

    normalized_target = " ".join(sorted(target_tokens))
    normalized_text = _normalize(text)
    name_text = _normalize(candidate.name or "")
    visible_text = _normalize(candidate.visible_text or "")

    if normalized_target and normalized_target in {name_text, visible_text, normalized_text}:
        score += 100
        reasons.append("exact_name_match")
    elif target_tokens and target_tokens.issubset(text_tokens):
        score += 70 + len(target_tokens) * 5
        reasons.append("all_target_terms_match")
    else:
        matches = target_tokens & text_tokens
        if matches:
            score += 25 * len(matches)
            reasons.append("partial_target_terms_match")

    if role and candidate.role == role:
        score += 20
        reasons.append("role_match")
    elif role:
        score -= 20

    if _is_stable_resource_link(candidate):
        score += 30
        reasons.append("stable_resource_link")

    if context_tokens:
        context_matches = context_tokens & text_tokens
        if context_matches:
            score += 22 * len(context_matches)
            reasons.append("context_terms_match")

    if candidate.location_bucket:
        score += 3
        reasons.append("location_available")

    if candidate.kind == "field" and candidate.input_type == "search":
        score += 15
        reasons.append("search_input_type")

    return max(score, 0), tuple(reasons)


def _is_stable_resource_link(candidate: SemanticCandidate) -> bool:
    if candidate.role != "link" or not candidate.target_url:
        return False
    parsed = urlsplit(candidate.target_url.strip())
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and bool(parsed.netloc)
        and _STABLE_RESOURCE_ID_PATTERN.search(parsed.path) is not None
    )


def _score_search_field(
    candidate: SemanticCandidate,
    context: str | None,
) -> tuple[int, tuple[str, ...]]:
    text_tokens = _tokens(_candidate_text(candidate))
    context_tokens = _tokens(context or "")
    reasons: list[str] = []
    score = 0

    if candidate.disabled:
        return 0, ("disabled",)
    if candidate.input_type == "search" or candidate.role == "searchbox":
        score += 90
        reasons.append("search_semantics")
    if text_tokens & _SEARCH_TERMS:
        score += 70
        reasons.append("search_terms_match")
    if candidate.role in _FIELD_ROLES:
        score += 15
        reasons.append("fillable_role")
    if context_tokens and context_tokens & text_tokens:
        score += 15
        reasons.append("context_terms_match")

    return score, tuple(reasons)


def _remap_resolution_from_candidates(
    source: SemanticCandidate,
    candidates: Sequence[SemanticCandidate],
    intent: NavigationIntent,
    *,
    ambiguity_margin: int,
) -> SemanticResolution:
    scored: list[SemanticCandidate] = []
    for candidate in candidates:
        score, reasons = _score_remap_candidate(source, candidate)
        if score >= _REMAP_MIN_SCORE:
            scored.append(_with_score(candidate, score, reasons))
    return _resolution_from_scored(
        intent,
        scored,
        ambiguity_margin=ambiguity_margin,
        not_found_message="No fresh semantic candidate matched the stale element fingerprint.",
    )


def _score_remap_candidate(
    source: SemanticCandidate,
    candidate: SemanticCandidate,
) -> tuple[int, tuple[str, ...]]:
    if candidate.disabled:
        return 0, ("disabled",)

    score = 0
    reasons: list[str] = []
    if source.fingerprint and source.fingerprint == candidate.fingerprint:
        score += 120
        reasons.append("fingerprint_match")
    if source.kind == candidate.kind:
        score += 10
        reasons.append("kind_match")
    if source.role == candidate.role:
        score += 20
        reasons.append("role_match")
    if _normalize(source.name or "") and _normalize(source.name or "") == _normalize(candidate.name or ""):
        score += 60
        reasons.append("name_match")
    if _normalize(source.visible_text or "") and _normalize(source.visible_text or "") == _normalize(candidate.visible_text or ""):
        score += 45
        reasons.append("visible_text_match")
    if source.target_url and source.target_url == candidate.target_url:
        score += 40
        reasons.append("target_url_match")
    if source.input_type and source.input_type == candidate.input_type:
        score += 15
        reasons.append("input_type_match")
    if source.location_bucket and source.location_bucket == candidate.location_bucket:
        score += 15
        reasons.append("location_bucket_match")
    elif source.location_region and source.location_region == candidate.location_region:
        score += 8
        reasons.append("location_region_match")

    source_tokens = _tokens(_candidate_text(source))
    candidate_tokens = _tokens(_candidate_text(candidate))
    token_overlap = source_tokens & candidate_tokens
    if token_overlap:
        score += min(len(token_overlap), 8) * 5
        reasons.append("semantic_terms_overlap")
    return score, tuple(reasons)


def _candidate_from_interactive(
    element: InteractiveElement,
    observation: PageObservation,
) -> SemanticCandidate:
    location_region = element.location.region if element.location else None
    location_bucket = _location_bucket(element.location)
    context = _compact_context(
        element.target_url,
        location_region,
        location_bucket,
        _surrounding_section_text(observation.sections, element.location),
    )
    return SemanticCandidate(
        candidate_id=element.element_id,
        kind="interactive",
        role=element.role,
        name=element.accessible_name or element.visible_text,
        visible_text=element.visible_text,
        context=context,
        target_url=element.target_url,
        input_type=element.input_type,
        location_region=location_region,
        location_bucket=location_bucket,
        fingerprint=_fingerprint(
            "interactive",
            element.role,
            element.accessible_name,
            element.visible_text,
            element.target_url,
            element.input_type,
            context,
            location_bucket,
        ),
        disabled=element.state.disabled,
    )


def _candidate_from_field(
    field: FormFieldSummary,
    observation: PageObservation,
) -> SemanticCandidate:
    location_region = field.location.region if field.location else None
    location_bucket = _location_bucket(field.location)
    context = _compact_context(
        field.field_name,
        field.input_type,
        location_region,
        location_bucket,
        _surrounding_section_text(observation.sections, field.location),
    )
    return SemanticCandidate(
        candidate_id=field.field_id,
        kind="field",
        role=field.role,
        name=field.label or field.placeholder or field.field_name,
        visible_text=field.placeholder,
        context=context,
        input_type=field.input_type,
        location_region=location_region,
        location_bucket=location_bucket,
        fingerprint=_fingerprint(
            "field",
            field.role,
            field.label,
            field.placeholder,
            field.field_name,
            field.input_type,
            context,
            location_bucket,
        ),
        disabled=field.state.disabled or field.state.readonly,
    )


def _is_click_candidate(element: InteractiveElement, requested_role: str | None) -> bool:
    if element.state.disabled:
        return False
    if requested_role:
        return element.role == requested_role
    if element.role in _CLICKABLE_ROLES:
        return True
    return element.target_url is not None


def _is_fillable_field(field: FormFieldSummary) -> bool:
    if field.state.disabled or field.state.readonly:
        return False
    if field.input_type in {"button", "submit", "reset", "hidden", "file"}:
        return False
    return field.role in _FIELD_ROLES or field.input_type in {
        "text",
        "search",
        "email",
        "url",
        "tel",
        "password",
        "number",
        "textarea",
        "select",
        "select-multiple",
        "contenteditable",
    }


def _is_search_intent(target: str) -> bool:
    return bool(_tokens(target) & _SEARCH_TERMS)


def _expanded_tokens(text: str) -> frozenset[str]:
    tokens = set(_tokens(text))
    if tokens & _SEARCH_TERMS:
        tokens.update(_SEARCH_TERMS)
    return frozenset(tokens)


def _candidate_text(candidate: SemanticCandidate) -> str:
    return " ".join(
        part
        for part in (
            candidate.role,
            candidate.name,
            candidate.visible_text,
            candidate.context,
            candidate.target_url,
            candidate.input_type,
            candidate.location_region,
            candidate.location_bucket,
        )
        if part
    )


def _with_score(
    candidate: SemanticCandidate,
    score: int,
    reasons: Sequence[str],
) -> SemanticCandidate:
    return SemanticCandidate(
        candidate_id=candidate.candidate_id,
        kind=candidate.kind,
        role=candidate.role,
        name=candidate.name,
        visible_text=candidate.visible_text,
        context=candidate.context,
        target_url=candidate.target_url,
        input_type=candidate.input_type,
        location_region=candidate.location_region,
        location_bucket=candidate.location_bucket,
        fingerprint=candidate.fingerprint,
        score=score,
        reasons=tuple(reasons),
        disabled=candidate.disabled,
    )


def _invalid(intent: NavigationIntent, message: str) -> SemanticResolution:
    return SemanticResolution(
        status=SemanticResolutionStatus.INVALID,
        intent=intent,
        message=message,
    )


def _normalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    return role.strip().casefold() or None


def _tokens(text: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _WORD_PATTERN.finditer(text))


def _normalize(text: str) -> str:
    return " ".join(sorted(_tokens(text)))


def _compact_context(*parts: str | None) -> str | None:
    text = " ".join(part for part in parts if part)
    return text or None


def _interactive_candidate_by_id(
    observation: PageObservation,
    element_id: str,
) -> SemanticCandidate | None:
    for element in observation.interactive_elements:
        if element.element_id == element_id:
            return _candidate_from_interactive(element, observation)
    return None


def _field_candidate_by_id(
    observation: PageObservation,
    field_id: str,
) -> SemanticCandidate | None:
    for field in observation.form_fields:
        if field.field_id == field_id:
            return _candidate_from_field(field, observation)
    return None


def _surrounding_section_text(
    sections: Sequence[SemanticSection],
    location: ElementLocation | None,
) -> str | None:
    if not sections:
        return None
    location_region = location.region if location else None
    matched = [
        section
        for section in sections
        if location_region
        and section.role in _LOCAL_CONTEXT_ROLES
        and section.location
        and section.location.region == location_region
    ]
    if not matched and location is not None:
        matched = [
            section
            for section in sections
            if section.role in _LOCAL_CONTEXT_ROLES
            and section.location is not None
            and _location_bucket(section.location) == _location_bucket(location)
        ]
    text = " ".join(
        _compact_context(section.role, section.heading, section.text) or ""
        for section in matched[:3]
    )
    return text[:600] or None


def _location_bucket(location: ElementLocation | None) -> str | None:
    if location is None or location.x_ratio is None or location.y_ratio is None:
        return location.region if location else None
    x_bucket = min(2, max(0, int(location.x_ratio * 3)))
    y_bucket = min(2, max(0, int(location.y_ratio * 3)))
    return f"{location.region}:{x_bucket}:{y_bucket}"


def _fingerprint(*parts: object) -> str:
    return _normalize(" ".join(str(part) for part in parts if part))


def _main_content_signature(observation: PageObservation) -> tuple[object, ...]:
    sections = tuple(
        (section.role, section.heading, _normalize(section.text))
        for section in observation.sections
        if section.role in _MAIN_CONTENT_ROLES
    )
    if not sections:
        sections = tuple(
            (section.role, section.heading, _normalize(section.text))
            for section in observation.sections[:6]
        )
    dialogs = tuple((dialog.role, dialog.title, _normalize(dialog.text)) for dialog in observation.dialogs)
    return (
        _normalize(observation.summary),
        sections[:8],
        dialogs[:4],
    )


def _observation_signature(observation: PageObservation) -> tuple[object, ...]:
    return (
        observation.summary,
        tuple(
            (section.role, section.heading, section.text)
            for section in observation.sections[:8]
        ),
        tuple(
            (
                element.element_id,
                element.role,
                element.accessible_name,
                element.visible_text,
                element.target_url,
            )
            for element in observation.interactive_elements[:12]
        ),
        tuple(
            (
                field.field_id,
                field.role,
                field.label,
                field.placeholder,
                field.value_state,
            )
            for field in observation.form_fields[:12]
        ),
    )
