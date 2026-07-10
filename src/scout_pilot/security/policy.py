"""Deterministic Security Policy Layer."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol

from scout_pilot.models import (
    ActionRisk,
    InteractiveElement,
    PageObservation,
    ToolRequest,
)
from scout_pilot.navigation import SemanticNavigationResolver


_WHITESPACE_PATTERN = re.compile(r"\s+")
_SENSITIVE_FIELD_HINTS = (
    "password",
    "token",
    "secret",
    "key",
    "credential",
    "value",
    "file",
    "path",
)
_SAFE_BROWSER_TOOLS = {
    "browser.navigate",
    "browser.observe",
    "browser.wait",
    "browser.screenshot",
    "browser.resolve_target",
    "browser.plan_form_fill",
}
_SAFE_KEYS = {
    "escape",
    "tab",
    "arrowup",
    "arrowdown",
    "arrowleft",
    "arrowright",
    "home",
    "end",
    "pageup",
    "pagedown",
}
_EXTERNAL_SIDE_EFFECT_TERMS = (
    "send",
    "submit",
    "apply",
    "application",
    "vacancy",
    "purchase",
    "buy",
    "checkout",
    "pay",
    "payment",
    "order",
    "confirm order",
    "publish",
    "post",
    "upload",
    "attach",
    "message",
    "email",
    "comment",
    "share",
    "отправ",
    "подать",
    "отклик",
    "ваканс",
    "куп",
    "оплат",
    "заказ",
    "опубликов",
    "загруз",
    "сообщ",
    "письм",
)
_DESTRUCTIVE_TERMS = (
    "delete",
    "remove",
    "trash",
    "spam",
    "unsubscribe",
    "cancel account",
    "wipe",
    "discard",
    "revoke",
    "удал",
    "корзин",
    "спам",
    "отпис",
    "отменить аккаунт",
    "стереть",
)
_SENSITIVE_TERMS = (
    "account settings",
    "settings",
    "password",
    "profile",
    "private file",
    "personal data",
    "address",
    "phone",
    "card",
    "resume",
    "cv",
    "настрой",
    "аккаунт",
    "парол",
    "профил",
    "личн",
    "телефон",
    "карта",
    "резюме",
)


@dataclass(frozen=True)
class ActionClassification:
    """Deterministic action classification."""

    risk: ActionRisk
    action: str
    expected_consequence: str
    matched_terms: tuple[str, ...] = ()
    source: str = "deterministic_policy"
    uncertain: bool = False

    def to_dict(self) -> Mapping[str, object]:
        return {
            "risk": self.risk.value,
            "action": self.action,
            "expected_consequence": self.expected_consequence,
            "matched_terms": list(self.matched_terms),
            "source": self.source,
            "uncertain": self.uncertain,
        }


@dataclass(frozen=True)
class SecurityConfirmationRequest:
    """Safe user-facing confirmation request."""

    confirmation_id: str
    request_signature: str
    tool_name: str
    risk: ActionRisk
    action: str
    expected_consequence: str
    message_ru: str
    redacted_arguments: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "confirmation_id": self.confirmation_id,
            "tool_name": self.tool_name,
            "risk": self.risk.value,
            "action": self.action,
            "expected_consequence": self.expected_consequence,
            "message_ru": self.message_ru,
            "redacted_arguments": dict(self.redacted_arguments),
        }


@dataclass(frozen=True)
class SecurityDecision:
    """Deterministic decision made before a tool can execute."""

    risk: ActionRisk
    allowed: bool
    requires_confirmation: bool
    reason: str
    classification: ActionClassification
    confirmation: SecurityConfirmationRequest | None = None
    blocked: bool = False
    audit_id: str | None = None

    def to_dict(self) -> Mapping[str, object]:
        return {
            "risk": self.risk.value,
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "reason": self.reason,
            "blocked": self.blocked,
            "audit_id": self.audit_id,
            "classification": self.classification.to_dict(),
            "confirmation": self.confirmation.to_dict() if self.confirmation else None,
        }


@dataclass(frozen=True)
class SecurityAuditEntry:
    """Internal audit trail entry for one policy decision."""

    audit_id: str
    request_signature: str
    tool_name: str
    risk: ActionRisk
    outcome: str
    reason: str
    matched_terms: tuple[str, ...] = ()
    confirmation_id: str | None = None

    def to_dict(self) -> Mapping[str, object]:
        return {
            "audit_id": self.audit_id,
            "request_signature": self.request_signature,
            "tool_name": self.tool_name,
            "risk": self.risk.value,
            "outcome": self.outcome,
            "reason": self.reason,
            "matched_terms": list(self.matched_terms),
            "confirmation_id": self.confirmation_id,
        }


@dataclass(frozen=True)
class SecurityEvaluationContext:
    """Safe context available to deterministic policy evaluation."""

    tool_description: str = ""
    validated_arguments: Mapping[str, object] = field(default_factory=dict)
    observation: PageObservation | None = None
    sensitive_fields: frozenset[str] = field(default_factory=frozenset)
    is_confirmed: bool = False


class SecurityPolicy(Protocol):
    """Classify and gate tool requests independently from the LLM."""

    def evaluate(
        self,
        request: ToolRequest,
        context: SecurityEvaluationContext | None = None,
    ) -> SecurityDecision:
        """Return a deterministic security decision for a tool request."""


@dataclass
class DeterministicSecurityPolicy:
    """Rule-based policy that cannot be controlled by the LLM."""

    _audit_trail: list[SecurityAuditEntry] = field(default_factory=list)
    _decision_index: int = 0

    @property
    def audit_trail(self) -> tuple[SecurityAuditEntry, ...]:
        return tuple(self._audit_trail)

    def evaluate(
        self,
        request: ToolRequest,
        context: SecurityEvaluationContext | None = None,
    ) -> SecurityDecision:
        context = context or SecurityEvaluationContext()
        sanitized_request = ToolRequest(
            name=request.name,
            arguments=dict(context.validated_arguments or request.arguments),
        )
        signature = build_security_request_signature(
            sanitized_request.name,
            sanitized_request.arguments,
        )
        classification = self.classify(sanitized_request, context)

        blocked = _is_blocked_navigation(sanitized_request)
        requires_confirmation = (
            not blocked
            and classification.risk is not ActionRisk.SAFE
            and not context.is_confirmed
        )
        allowed = not blocked and not requires_confirmation
        reason = _decision_reason(
            classification,
            allowed=allowed,
            blocked=blocked,
            confirmed=context.is_confirmed,
        )
        confirmation = (
            self._confirmation_request(
                sanitized_request,
                classification,
                signature,
                context,
            )
            if requires_confirmation
            else None
        )
        audit_id = self._next_audit_id()
        decision = SecurityDecision(
            risk=classification.risk,
            allowed=allowed,
            requires_confirmation=requires_confirmation,
            reason=reason,
            classification=classification,
            confirmation=confirmation,
            blocked=blocked,
            audit_id=audit_id,
        )
        self._audit_trail.append(
            SecurityAuditEntry(
                audit_id=audit_id,
                request_signature=signature,
                tool_name=sanitized_request.name,
                risk=classification.risk,
                outcome=_outcome_for_decision(decision),
                reason=reason,
                matched_terms=classification.matched_terms,
                confirmation_id=confirmation.confirmation_id if confirmation else None,
            )
        )
        return decision

    def classify(
        self,
        request: ToolRequest,
        context: SecurityEvaluationContext | None = None,
    ) -> ActionClassification:
        context = context or SecurityEvaluationContext()
        if request.name in _SAFE_BROWSER_TOOLS:
            return ActionClassification(
                risk=ActionRisk.SAFE,
                action=_action_for_safe_tool(request.name),
                expected_consequence="Действие только открывает, читает или диагностирует страницу.",
            )

        if request.name in {"browser.fill", "browser.fill_by_label"}:
            return ActionClassification(
                risk=ActionRisk.SENSITIVE,
                action="ввести данные в поле формы",
                expected_consequence=(
                    "Данные будут введены на странице, но не должны отправляться без отдельного подтверждения."
                ),
                matched_terms=("browser.fill",),
            )

        if request.name == "browser.press_key":
            return _classify_key_press(request)

        if request.name == "browser.click":
            return _classify_click(request, context)

        if request.name == "browser.click_by_intent":
            return _classify_click_intent(request, context)

        text = _classification_text(request, context)
        matched = _matched_terms(text, _DESTRUCTIVE_TERMS)
        if matched:
            return ActionClassification(
                risk=ActionRisk.DESTRUCTIVE,
                action="выполнить действие, которое может удалить или переместить данные",
                expected_consequence="Данные могут быть удалены, перемещены в корзину или спам.",
                matched_terms=matched,
            )
        matched = _matched_terms(text, _EXTERNAL_SIDE_EFFECT_TERMS)
        if matched:
            return ActionClassification(
                risk=ActionRisk.EXTERNAL_SIDE_EFFECT,
                action="выполнить действие с внешним эффектом",
                expected_consequence="Данные могут быть отправлены или опубликованы во внешнем сервисе.",
                matched_terms=matched,
            )
        matched = _matched_terms(text, _SENSITIVE_TERMS)
        if matched:
            return ActionClassification(
                risk=ActionRisk.SENSITIVE,
                action="изменить или использовать чувствительные данные",
                expected_consequence="Могут быть изменены личные данные, настройки или приватная информация.",
                matched_terms=matched,
            )
        return ActionClassification(
            risk=ActionRisk.SAFE,
            action="выполнить безопасный инструмент",
            expected_consequence="Действие не выглядит отправкой, удалением, покупкой или публикацией данных.",
        )

    def _confirmation_request(
        self,
        request: ToolRequest,
        classification: ActionClassification,
        signature: str,
        context: SecurityEvaluationContext,
    ) -> SecurityConfirmationRequest:
        confirmation_id = f"confirm_{self._decision_index + 1}"
        message = (
            "Требуется подтверждение: "
            f"{classification.action}. "
            f"Ожидаемое последствие: {classification.expected_consequence} "
            "Подтвердите действие явно, если хотите продолжить."
        )
        return SecurityConfirmationRequest(
            confirmation_id=confirmation_id,
            request_signature=signature,
            tool_name=request.name,
            risk=classification.risk,
            action=classification.action,
            expected_consequence=classification.expected_consequence,
            message_ru=message,
            redacted_arguments=_redact_arguments(
                request.arguments,
                sensitive_fields=context.sensitive_fields,
            ),
        )

    def _next_audit_id(self) -> str:
        self._decision_index += 1
        return f"security_decision_{self._decision_index}"


def build_security_request_signature(
    tool_name: str,
    arguments: Mapping[str, object],
) -> str:
    """Build a stable private signature for an exact tool request."""

    payload = json.dumps(
        {"name": tool_name, "arguments": dict(arguments)},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _classify_key_press(request: ToolRequest) -> ActionClassification:
    key = str(request.arguments.get("key", "")).casefold().strip()
    normalized_key = key.replace("+", "").replace("-", "").replace("_", "").replace(" ", "")
    if normalized_key in _SAFE_KEYS:
        return ActionClassification(
            risk=ActionRisk.SAFE,
            action=f"нажать клавишу {key or 'безопасной навигации'}",
            expected_consequence="Клавиша используется для навигации по странице или отмены действия.",
        )
    if "enter" in normalized_key or "return" in normalized_key:
        return ActionClassification(
            risk=ActionRisk.EXTERNAL_SIDE_EFFECT,
            action="нажать Enter",
            expected_consequence="Фокусированная форма может быть отправлена во внешний сервис.",
            matched_terms=("enter",),
            uncertain=True,
        )
    return ActionClassification(
        risk=ActionRisk.SENSITIVE,
        action=f"нажать клавишу {key or 'на странице'}",
        expected_consequence="Клавиша может изменить состояние страницы или отправить активную форму.",
        uncertain=True,
    )


def _classify_click(
    request: ToolRequest,
    context: SecurityEvaluationContext,
) -> ActionClassification:
    element = _find_interactive_element(
        context.observation,
        str(request.arguments.get("element_id", "")),
    )
    text = _element_text(element) if element is not None else _classification_text(request, context)
    if element is None:
        return ActionClassification(
            risk=ActionRisk.EXTERNAL_SIDE_EFFECT,
            action="нажать неизвестный элемент страницы",
            expected_consequence=(
                "Без актуального семантического описания нельзя надежно исключить отправку, покупку или удаление."
            ),
            uncertain=True,
        )

    matched = _matched_terms(text, _DESTRUCTIVE_TERMS)
    if matched:
        return ActionClassification(
            risk=ActionRisk.DESTRUCTIVE,
            action=f"нажать «{_element_name(element)}»",
            expected_consequence="Данные могут быть удалены, отменены или перемещены в корзину/спам.",
            matched_terms=matched,
        )
    matched = _matched_terms(text, _EXTERNAL_SIDE_EFFECT_TERMS)
    if matched:
        return ActionClassification(
            risk=ActionRisk.EXTERNAL_SIDE_EFFECT,
            action=f"нажать «{_element_name(element)}»",
            expected_consequence="Действие может отправить данные, отклик, сообщение, заказ или публикацию.",
            matched_terms=matched,
        )
    matched = _matched_terms(text, _SENSITIVE_TERMS)
    if matched:
        return ActionClassification(
            risk=ActionRisk.SENSITIVE,
            action=f"нажать «{_element_name(element)}»",
            expected_consequence="Действие может изменить настройки или затронуть личные данные.",
            matched_terms=matched,
        )
    return ActionClassification(
        risk=ActionRisk.SAFE,
        action=f"нажать «{_element_name(element)}»",
        expected_consequence="Элемент не похож на отправку, удаление, покупку, публикацию или изменение настроек.",
    )


def _classify_click_intent(
    request: ToolRequest,
    context: SecurityEvaluationContext,
) -> ActionClassification:
    target = str(request.arguments.get("target", "")).strip() or "элемент по семантическому намерению"
    if context.observation is not None and target:
        resolution = SemanticNavigationResolver().resolve_click(
            context.observation,
            target=target,
            role=_optional_argument(request.arguments, "role"),
            context=_optional_argument(request.arguments, "context"),
        )
        if resolution.is_resolved and resolution.selected is not None:
            candidate = resolution.selected
            return _classification_from_action_text(
                _normalize(
                    " ".join(
                        str(value)
                        for value in (
                            candidate.role,
                            candidate.name,
                            candidate.visible_text,
                            candidate.context,
                            candidate.target_url,
                            candidate.input_type,
                        )
                        if value
                    )
                ),
                action=f"нажать «{candidate.name or target}»",
            )
        if resolution.status.value == "ambiguous":
            return ActionClassification(
                risk=ActionRisk.EXTERNAL_SIDE_EFFECT,
                action=f"нажать «{target}»",
                expected_consequence=(
                    "Намерение совпало с несколькими элементами, поэтому нельзя надежно исключить внешний эффект."
                ),
                matched_terms=("ambiguous_semantic_target",),
                uncertain=True,
            )

    text = _classification_text(request, context)
    return _classification_from_action_text(text, action=f"нажать «{target}»")


def _classification_from_action_text(text: str, *, action: str) -> ActionClassification:
    normalized = _normalize(text)

    matched = _matched_terms(normalized, _DESTRUCTIVE_TERMS)
    if matched:
        return ActionClassification(
            risk=ActionRisk.DESTRUCTIVE,
            action=action,
            expected_consequence="Данные могут быть удалены, отменены или перемещены в корзину/спам.",
            matched_terms=matched,
        )
    matched = _matched_terms(normalized, _EXTERNAL_SIDE_EFFECT_TERMS)
    if matched:
        return ActionClassification(
            risk=ActionRisk.EXTERNAL_SIDE_EFFECT,
            action=action,
            expected_consequence="Действие может отправить данные, отклик, сообщение, заказ или публикацию.",
            matched_terms=matched,
        )
    matched = _matched_terms(normalized, _SENSITIVE_TERMS)
    if matched:
        return ActionClassification(
            risk=ActionRisk.SENSITIVE,
            action=action,
            expected_consequence="Действие может изменить настройки или затронуть личные данные.",
            matched_terms=matched,
        )
    return ActionClassification(
        risk=ActionRisk.SAFE,
        action=action,
        expected_consequence="Намерение не похоже на отправку, удаление, покупку, публикацию или изменение настроек.",
    )


def _is_blocked_navigation(request: ToolRequest) -> bool:
    if request.name != "browser.navigate":
        return False
    url = str(request.arguments.get("url", "")).strip().casefold()
    return url.startswith(("javascript:", "file:"))


def _decision_reason(
    classification: ActionClassification,
    *,
    allowed: bool,
    blocked: bool,
    confirmed: bool,
) -> str:
    if blocked:
        return "Navigation target is blocked by deterministic security policy."
    if allowed and confirmed:
        return "Action was explicitly confirmed by the user."
    if allowed:
        return "Action is classified as safe by deterministic security policy."
    return "Action requires explicit user confirmation before execution."


def _outcome_for_decision(decision: SecurityDecision) -> str:
    if decision.blocked:
        return "blocked"
    if decision.requires_confirmation:
        return "confirmation_required"
    return "allowed"


def _action_for_safe_tool(tool_name: str) -> str:
    if tool_name == "browser.navigate":
        return "открыть страницу"
    if tool_name == "browser.observe":
        return "прочитать состояние страницы"
    if tool_name == "browser.screenshot":
        return "сделать диагностический скриншот"
    if tool_name == "browser.wait":
        return "подождать обновления страницы"
    return "выполнить безопасное действие"


def _classification_text(
    request: ToolRequest,
    context: SecurityEvaluationContext,
) -> str:
    parts = [
        request.name,
        context.tool_description,
        *(str(value) for value in request.arguments.values()),
    ]
    return _normalize(" ".join(parts))


def _optional_argument(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _find_interactive_element(
    observation: PageObservation | None,
    element_id: str,
) -> InteractiveElement | None:
    if observation is None or not element_id:
        return None
    return next(
        (
            element
            for element in observation.interactive_elements
            if element.element_id == element_id
        ),
        None,
    )


def _element_text(element: InteractiveElement) -> str:
    return _normalize(
        " ".join(
            value
            for value in (
                element.role,
                element.accessible_name,
                element.visible_text,
                element.target_url,
                element.input_type,
            )
            if value
        )
    )


def _element_name(element: InteractiveElement) -> str:
    return (
        element.accessible_name
        or element.visible_text
        or element.target_url
        or element.element_id
    )


def _matched_terms(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    normalized = _normalize(text)
    return tuple(term for term in terms if term in normalized)


def _normalize(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip().casefold()


def _redact_arguments(
    arguments: Mapping[str, object],
    *,
    sensitive_fields: frozenset[str],
) -> Mapping[str, object]:
    redacted: dict[str, object] = {}
    for key, value in arguments.items():
        if key in sensitive_fields or any(hint in key.casefold() for hint in _SENSITIVE_FIELD_HINTS):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted
