"""Context Budgeting and Compression implementation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from scout_pilot.models import (
    ContextBudget,
    DialogSummary,
    PageIssue,
    PageIssueCode,
    PageObservation,
    SemanticSection,
)


_RAW_MARKUP_PATTERN = re.compile(
    r"(?is)<\s*(html|head|body|script|style|form|input|button|div|span|a)\b"
)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_CRITICAL_MEMORY_PATTERNS = (
    "user_goal",
    "constraint",
    "confirmed_choice",
    "confirmation",
    "confirmed",
    "security",
    "warning",
    "requires_confirmation",
)
_RECENT_FAILURE_PATTERNS = (
    "failure",
    "failed",
    "retry",
    "replan",
    "reflection",
    "blocked",
)
_LOW_VALUE_MEMORY_PATTERNS = (
    "working.observation",
    "observation_",
    "navigation",
    "header",
    "footer",
)


class TokenEstimator(Protocol):
    """Provider-neutral token estimation interface."""

    def estimate_text_tokens(self, text: str) -> int:
        """Estimate token count for text."""

    def estimate_value_tokens(self, value: object) -> int:
        """Estimate token count for a JSON-like value."""


@dataclass(frozen=True)
class HeuristicTokenEstimator:
    """Fallback token estimator that does not depend on provider SDKs."""

    chars_per_token: int = 4

    def estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        word_count = len(text.split())
        char_estimate = (len(text) + self.chars_per_token - 1) // self.chars_per_token
        return max(1, max(word_count, char_estimate))

    def estimate_value_tokens(self, value: object) -> int:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            text = str(value)
        return self.estimate_text_tokens(text)


@dataclass(frozen=True)
class ContextBudgetSettings:
    """Limits used to assemble model-facing context."""

    max_input_tokens: int = 8000
    reserved_output_tokens: int = 1200
    max_observation_tokens: int = 3500
    max_memory_tokens: int = 1400
    max_memory_summaries: int = 12
    max_section_chars: int = 800
    max_summary_chars: int = 900
    emergency_observation_tokens: int = 900
    emergency_memory_tokens: int = 500

    @property
    def available_input_tokens(self) -> int:
        return max(self.max_input_tokens - self.reserved_output_tokens, 256)


@dataclass(frozen=True)
class ContextCompressionMetrics:
    """Transparent before/after context budgeting metrics."""

    before_tokens: int
    after_tokens: int
    observation_before_tokens: int = 0
    observation_after_tokens: int = 0
    memory_before_tokens: int = 0
    memory_after_tokens: int = 0
    dropped_sections: int = 0
    dropped_interactive_elements: int = 0
    dropped_form_fields: int = 0
    dropped_memory_summaries: int = 0
    deduplicated_items: int = 0
    preserved_critical_facts: int = 0
    emergency_compression_applied: bool = False

    def to_dict(self) -> Mapping[str, int | bool]:
        return {
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "observation_before_tokens": self.observation_before_tokens,
            "observation_after_tokens": self.observation_after_tokens,
            "memory_before_tokens": self.memory_before_tokens,
            "memory_after_tokens": self.memory_after_tokens,
            "dropped_sections": self.dropped_sections,
            "dropped_interactive_elements": self.dropped_interactive_elements,
            "dropped_form_fields": self.dropped_form_fields,
            "dropped_memory_summaries": self.dropped_memory_summaries,
            "deduplicated_items": self.deduplicated_items,
            "preserved_critical_facts": self.preserved_critical_facts,
            "emergency_compression_applied": self.emergency_compression_applied,
        }


@dataclass(frozen=True)
class BudgetedContext:
    """Budgeted model-facing context payload."""

    observation: PageObservation | None
    memory_summaries: tuple[str, ...]
    budget: Mapping[str, int]
    metrics: ContextCompressionMetrics


@dataclass(frozen=True)
class _ObservationFit:
    observation: PageObservation | None
    metrics: ContextCompressionMetrics


@dataclass(frozen=True)
class _MemoryFit:
    summaries: tuple[str, ...]
    before_tokens: int
    after_tokens: int
    dropped: int
    deduplicated: int
    preserved_critical: int


class ContextBudgeter(Protocol):
    """Prepare page observations for bounded LLM context windows."""

    def estimate_observation_tokens(self, observation: PageObservation) -> int:
        """Estimate tokens required by an observation."""

    def fit_observation(
        self,
        observation: PageObservation,
        budget: ContextBudget,
    ) -> PageObservation:
        """Return an observation that fits the available context budget."""

    def fit_memory_summaries(
        self,
        summaries: Sequence[str],
        max_tokens: int | None = None,
        max_items: int | None = None,
    ) -> tuple[str, ...]:
        """Return bounded memory summaries for model-facing context."""

    def assemble(
        self,
        *,
        user_task: str,
        observation: PageObservation | None,
        memory_summaries: Sequence[str],
        budget: Mapping[str, int] | None = None,
        max_input_tokens: int | None = None,
        reserved_output_tokens: int | None = None,
    ) -> BudgetedContext:
        """Assemble a complete bounded model-facing context payload."""


@dataclass
class DeterministicContextBudgeter:
    """Provider-neutral context compressor for observations and memory."""

    settings: ContextBudgetSettings = field(default_factory=ContextBudgetSettings)
    estimator: TokenEstimator = field(default_factory=HeuristicTokenEstimator)
    last_metrics: ContextCompressionMetrics | None = None

    def estimate_text_tokens(self, text: str) -> int:
        return self.estimator.estimate_text_tokens(text)

    def estimate_value_tokens(self, value: object) -> int:
        return self.estimator.estimate_value_tokens(value)

    def estimate_observation_tokens(self, observation: PageObservation) -> int:
        return self.estimator.estimate_value_tokens(observation.to_llm_context())

    def fit_observation(
        self,
        observation: PageObservation,
        budget: ContextBudget,
    ) -> PageObservation:
        limit = max(budget.remaining_tokens, 128)
        fit = self._fit_observation(observation, limit)
        self.last_metrics = fit.metrics
        if fit.observation is None:
            return observation
        return fit.observation

    def fit_memory_summaries(
        self,
        summaries: Sequence[str],
        max_tokens: int | None = None,
        max_items: int | None = None,
    ) -> tuple[str, ...]:
        fit = self._fit_memory_summaries(
            summaries,
            max_tokens=max_tokens or self.settings.max_memory_tokens,
            max_items=max_items or self.settings.max_memory_summaries,
        )
        return fit.summaries

    def assemble(
        self,
        *,
        user_task: str,
        observation: PageObservation | None,
        memory_summaries: Sequence[str],
        budget: Mapping[str, int] | None = None,
        max_input_tokens: int | None = None,
        reserved_output_tokens: int | None = None,
    ) -> BudgetedContext:
        """Assemble bounded context for any model-facing request."""

        max_tokens = int(max_input_tokens or self.settings.max_input_tokens)
        reserved = int(reserved_output_tokens or self.settings.reserved_output_tokens)
        available = max(max_tokens - reserved, 256)
        explicit_remaining = int((budget or {}).get("remaining_tokens", available))
        available = min(available, max(explicit_remaining, 256))

        base_tokens = self.estimator.estimate_value_tokens(
            {
                "user_task": _safe_text(user_task, self.settings.max_summary_chars),
                "budget": dict(budget or {}),
            }
        )
        observation_limit = min(
            self.settings.max_observation_tokens,
            max(available - base_tokens - self.settings.max_memory_tokens, 256),
        )
        observation_fit = self._fit_observation(observation, observation_limit)
        memory_limit = min(
            self.settings.max_memory_tokens,
            max(available - base_tokens - observation_fit.metrics.observation_after_tokens, 128),
        )
        memory_fit = self._fit_memory_summaries(
            memory_summaries,
            max_tokens=memory_limit,
            max_items=self.settings.max_memory_summaries,
        )

        before_tokens = (
            base_tokens
            + observation_fit.metrics.observation_before_tokens
            + memory_fit.before_tokens
        )
        after_tokens = (
            base_tokens
            + observation_fit.metrics.observation_after_tokens
            + memory_fit.after_tokens
        )
        emergency = observation_fit.metrics.emergency_compression_applied
        if after_tokens > available:
            emergency = True
            emergency_memory = self._fit_memory_summaries(
                memory_fit.summaries,
                max_tokens=self.settings.emergency_memory_tokens,
                max_items=max(3, self.settings.max_memory_summaries // 2),
            )
            emergency_observation = self._fit_observation(
                observation_fit.observation,
                self.settings.emergency_observation_tokens,
            )
            memory_fit = emergency_memory
            observation_fit = emergency_observation
            after_tokens = (
                base_tokens
                + observation_fit.metrics.observation_after_tokens
                + memory_fit.after_tokens
            )

        metrics = ContextCompressionMetrics(
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            observation_before_tokens=observation_fit.metrics.observation_before_tokens,
            observation_after_tokens=observation_fit.metrics.observation_after_tokens,
            memory_before_tokens=memory_fit.before_tokens,
            memory_after_tokens=memory_fit.after_tokens,
            dropped_sections=observation_fit.metrics.dropped_sections,
            dropped_interactive_elements=observation_fit.metrics.dropped_interactive_elements,
            dropped_form_fields=observation_fit.metrics.dropped_form_fields,
            dropped_memory_summaries=memory_fit.dropped,
            deduplicated_items=observation_fit.metrics.deduplicated_items
            + memory_fit.deduplicated,
            preserved_critical_facts=memory_fit.preserved_critical,
            emergency_compression_applied=emergency,
        )
        self.last_metrics = metrics
        merged_budget = {
            **dict(budget or {}),
            "max_input_tokens": max_tokens,
            "reserved_output_tokens": reserved,
            "estimated_input_tokens_before": metrics.before_tokens,
            "estimated_input_tokens_after": metrics.after_tokens,
            "remaining_tokens": max(available - metrics.after_tokens, 0),
        }
        return BudgetedContext(
            observation=observation_fit.observation,
            memory_summaries=memory_fit.summaries,
            budget=merged_budget,
            metrics=metrics,
        )

    def _fit_observation(
        self,
        observation: PageObservation | None,
        max_tokens: int,
    ) -> _ObservationFit:
        if observation is None:
            metrics = ContextCompressionMetrics(before_tokens=0, after_tokens=0)
            return _ObservationFit(observation=None, metrics=metrics)

        before_tokens = self.estimate_observation_tokens(observation)
        deduped, deduped_count = _dedupe_observation(observation, self.settings)
        fitted = _truncate_observation_text(deduped, self.settings)
        dropped_sections = len(observation.sections) - len(fitted.sections)
        dropped_interactive = len(observation.interactive_elements) - len(
            fitted.interactive_elements
        )
        dropped_fields = len(observation.form_fields) - len(fitted.form_fields)

        while self.estimate_observation_tokens(fitted) > max_tokens and fitted.sections:
            fitted = replace(fitted, sections=fitted.sections[:-1])
            dropped_sections += 1

        while (
            self.estimate_observation_tokens(fitted) > max_tokens
            and len(fitted.interactive_elements) > 8
        ):
            fitted = replace(fitted, interactive_elements=fitted.interactive_elements[:-1])
            dropped_interactive += 1

        while (
            self.estimate_observation_tokens(fitted) > max_tokens
            and len(fitted.form_fields) > 4
        ):
            fitted = replace(fitted, form_fields=fitted.form_fields[:-1])
            dropped_fields += 1

        emergency = False
        if self.estimate_observation_tokens(fitted) > max_tokens:
            emergency = True
            fitted = _emergency_observation(fitted, self.settings)

        after_tokens = self.estimate_observation_tokens(fitted)
        if (
            before_tokens > after_tokens
            or deduped_count
            or emergency
            or dropped_sections
            or dropped_interactive
            or dropped_fields
        ):
            fitted = _with_truncation_issue(fitted)
            after_tokens = self.estimate_observation_tokens(fitted)
            while (
                after_tokens > max_tokens
                and len(fitted.interactive_elements) > 1
            ):
                fitted = replace(fitted, interactive_elements=fitted.interactive_elements[:-1])
                dropped_interactive += 1
                after_tokens = self.estimate_observation_tokens(fitted)
            while after_tokens > max_tokens and fitted.form_fields:
                fitted = replace(fitted, form_fields=fitted.form_fields[:-1])
                dropped_fields += 1
                after_tokens = self.estimate_observation_tokens(fitted)
            while after_tokens > max_tokens and fitted.dialogs:
                fitted = replace(fitted, dialogs=fitted.dialogs[:-1])
                after_tokens = self.estimate_observation_tokens(fitted)
            while after_tokens > max_tokens and len(fitted.summary) > 80:
                shortened = fitted.summary[: max(80, len(fitted.summary) // 2)].rstrip()
                if len(shortened) >= len(fitted.summary):
                    break
                fitted = replace(
                    fitted,
                    summary=shortened,
                )
                after_tokens = self.estimate_observation_tokens(fitted)

        metrics = ContextCompressionMetrics(
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            observation_before_tokens=before_tokens,
            observation_after_tokens=after_tokens,
            dropped_sections=max(dropped_sections, 0),
            dropped_interactive_elements=max(dropped_interactive, 0),
            dropped_form_fields=max(dropped_fields, 0),
            deduplicated_items=deduped_count,
            emergency_compression_applied=emergency,
        )
        return _ObservationFit(observation=fitted, metrics=metrics)

    def _fit_memory_summaries(
        self,
        summaries: Sequence[str],
        *,
        max_tokens: int,
        max_items: int,
    ) -> _MemoryFit:
        before_tokens = self.estimator.estimate_value_tokens(list(summaries))
        scored: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        deduped = 0
        for index, summary in enumerate(summaries):
            text = _safe_text(str(summary), self.settings.max_summary_chars)
            if not text:
                continue
            key = _normalize(text)
            if key in seen:
                deduped += 1
                continue
            seen.add(key)
            scored.append((_memory_priority(text), index, text))

        selected: list[str] = []
        tokens = 0
        preserved_critical = 0
        for priority, index, text in sorted(scored, key=lambda item: (-item[0], -item[1])):
            token_count = self.estimator.estimate_text_tokens(text)
            is_critical = priority >= 90
            if len(selected) >= max_items and not is_critical:
                continue
            if tokens + token_count > max_tokens and not is_critical:
                continue
            if tokens + token_count > max_tokens and selected:
                continue
            selected.append(text)
            tokens += token_count
            if is_critical:
                preserved_critical += 1

        selected = _restore_original_order(selected, summaries)
        after_tokens = self.estimator.estimate_value_tokens(list(selected))
        return _MemoryFit(
            summaries=tuple(selected),
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            dropped=max(len(summaries) - len(selected) - deduped, 0),
            deduplicated=deduped,
            preserved_critical=preserved_critical,
        )


def _dedupe_observation(
    observation: PageObservation,
    settings: ContextBudgetSettings,
) -> tuple[PageObservation, int]:
    deduped_count = 0
    sections, section_dupes = _dedupe_sequence(
        _prioritized_sections(observation.sections, settings),
        key=_section_dedupe_key,
    )
    elements, element_dupes = _dedupe_sequence(
        observation.interactive_elements,
        key=lambda element: _normalize(
            f"{element.role}:{element.accessible_name}:{element.visible_text}:{element.target_url}"
        ),
    )
    fields, field_dupes = _dedupe_sequence(
        observation.form_fields,
        key=lambda field: _normalize(
            f"{field.role}:{field.input_type}:{field.label}:{field.placeholder}:{field.value_state}"
        ),
    )
    dialogs, dialog_dupes = _dedupe_sequence(
        observation.dialogs,
        key=lambda dialog: _normalize(f"{dialog.role}:{dialog.title}:{dialog.text}"),
    )
    deduped_count += section_dupes + element_dupes + field_dupes + dialog_dupes
    return (
        replace(
            observation,
            sections=tuple(sections),
            interactive_elements=tuple(elements),
            form_fields=tuple(fields),
            dialogs=tuple(dialogs),
        ),
        deduped_count,
    )


def _section_dedupe_key(section: SemanticSection) -> str:
    normalized_text = _normalize(section.text)
    if _section_priority(section) <= 20:
        return normalized_text
    return _normalize(f"{section.role}:{section.heading}:{section.text}")


def _prioritized_sections(
    sections: Sequence[SemanticSection],
    settings: ContextBudgetSettings,
) -> tuple[SemanticSection, ...]:
    sanitized = [
        replace(section, text=_safe_text(section.text, settings.max_section_chars))
        for section in sections
        if _safe_text(section.text, settings.max_section_chars)
    ]
    return tuple(
        section
        for _, _, section in sorted(
            (
                (_section_priority(section), index, section)
                for index, section in enumerate(sanitized)
            ),
            key=lambda item: (-item[0], item[1]),
        )
    )


def _section_priority(section: SemanticSection) -> int:
    text = f"{section.role} {section.heading or ''} {section.text}".casefold()
    if any(term in text for term in ("dialog", "modal", "alert", "error", "warning")):
        return 110
    if any(term in text for term in ("selected", "focused", "form", "search", "result")):
        return 100
    if any(term in text for term in ("main", "article", "content")):
        return 80
    if any(term in text for term in ("navigation", "nav", "header", "footer", "menu")):
        return 20
    return 50


def _truncate_observation_text(
    observation: PageObservation,
    settings: ContextBudgetSettings,
) -> PageObservation:
    summary = _safe_text(observation.summary, settings.max_summary_chars)
    sections = tuple(
        replace(section, text=_safe_text(section.text, settings.max_section_chars))
        for section in observation.sections
        if _safe_text(section.text, settings.max_section_chars)
    )
    dialogs = tuple(_fit_dialog(dialog, settings) for dialog in observation.dialogs)
    return replace(observation, summary=summary, sections=sections, dialogs=dialogs)


def _fit_dialog(dialog: DialogSummary, settings: ContextBudgetSettings) -> DialogSummary:
    return replace(dialog, text=_safe_text(dialog.text, settings.max_section_chars))


def _emergency_observation(
    observation: PageObservation,
    settings: ContextBudgetSettings,
) -> PageObservation:
    return replace(
        observation,
        summary=_safe_text(observation.summary, min(settings.max_summary_chars, 320)),
        sections=(),
        interactive_elements=observation.interactive_elements[:5],
        form_fields=observation.form_fields[:3],
        dialogs=observation.dialogs[:2],
        focused_element=observation.focused_element,
    )


def _with_truncation_issue(observation: PageObservation) -> PageObservation:
    if any(issue.code is PageIssueCode.OBSERVATION_TRUNCATED for issue in observation.issues):
        return observation
    return replace(
        observation,
        issues=(
            *observation.issues,
            PageIssue(
                code=PageIssueCode.OBSERVATION_TRUNCATED,
                message="Observation was compressed for the model context budget.",
            ),
        ),
    )


def _dedupe_sequence(values: Sequence[Any], *, key) -> tuple[list[Any], int]:
    seen: set[str] = set()
    output = []
    duplicates = 0
    for value in values:
        normalized = key(value)
        if not normalized:
            continue
        if normalized in seen:
            duplicates += 1
            continue
        seen.add(normalized)
        output.append(value)
    return output, duplicates


def _safe_text(value: str, max_chars: int) -> str:
    if _RAW_MARKUP_PATTERN.search(value) and value.count("<") >= 3:
        return ""
    compact = _WHITESPACE_PATTERN.sub(" ", value).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."


def _normalize(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip().casefold()


def _memory_priority(summary: str) -> int:
    lowered = summary.casefold()
    if any(pattern in lowered for pattern in _CRITICAL_MEMORY_PATTERNS):
        return 100
    if any(pattern in lowered for pattern in _RECENT_FAILURE_PATTERNS):
        return 85
    if any(pattern in lowered for pattern in _LOW_VALUE_MEMORY_PATTERNS):
        return 20
    if lowered.startswith("task."):
        return 80
    if lowered.startswith("episodic."):
        return 60
    return 50


def _restore_original_order(selected: Sequence[str], original: Sequence[str]) -> list[str]:
    selected_keys = {_normalize(value) for value in selected}
    output = []
    for value in original:
        text = str(value)
        if _normalize(text) in selected_keys:
            output.append(text)
            selected_keys.remove(_normalize(text))
    for value in selected:
        if _normalize(value) in selected_keys:
            output.append(value)
    return output
