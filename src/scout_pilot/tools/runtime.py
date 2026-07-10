"""Provider-neutral tool runtime."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Awaitable, Callable, Protocol

from scout_pilot.models import ToolRequest
from scout_pilot.security import (
    DeterministicSecurityPolicy,
    SecurityConfirmationRequest,
    SecurityDecision,
    SecurityEvaluationContext,
    SecurityPolicy,
    build_security_request_signature,
)
from scout_pilot.tools.base import BaseTool, ToolContext
from scout_pilot.tools.registry import ToolRegistry
from scout_pilot.tools.types import (
    PreExecutionDecision,
    PreExecutionStatus,
    ToolExecutionOutcome,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolFailureKind,
    ToolHistoryEntry,
    ToolValidationError,
)


logger = logging.getLogger(__name__)

PreExecutionHook = Callable[
    [ToolRequest, BaseTool, dict[str, object]],
    PreExecutionDecision | Awaitable[PreExecutionDecision],
]


class ToolRuntime(Protocol):
    """Execute registered tools through a stable request/result contract."""

    async def execute(self, request: ToolRequest) -> ToolExecutionResult:
        """Execute a tool request after policy checks have passed."""


class DefaultToolRuntime:
    """Validate, execute and record provider-neutral tool requests."""

    def __init__(
        self,
        registry: ToolRegistry,
        context: ToolContext,
        pre_execution_hook: PreExecutionHook | None = None,
        security_policy: SecurityPolicy | None = None,
        history_limit: int = 100,
    ) -> None:
        self._registry = registry
        self._context = context
        self._pre_execution_hook = pre_execution_hook
        self._security_policy = security_policy or DeterministicSecurityPolicy()
        self._history_limit = history_limit
        self._history: list[ToolHistoryEntry] = []
        self._pending_confirmations: dict[str, SecurityConfirmationRequest] = {}
        self._confirmed_signatures: set[str] = set()

    @property
    def history(self) -> tuple[ToolHistoryEntry, ...]:
        return tuple(self._history)

    @property
    def pending_confirmations(self) -> tuple[SecurityConfirmationRequest, ...]:
        return tuple(self._pending_confirmations.values())

    @property
    def security_audit_trail(self):
        return tuple(getattr(self._security_policy, "audit_trail", ()))

    def confirm_pending_action(self, confirmation_id: str) -> bool:
        """Allow one exact pending request after explicit user confirmation."""

        confirmation = self._pending_confirmations.pop(confirmation_id, None)
        if confirmation is None:
            return False
        self._confirmed_signatures.add(confirmation.request_signature)
        return True

    def reject_pending_action(self, confirmation_id: str) -> bool:
        """Discard a pending request without allowing execution."""

        return self._pending_confirmations.pop(confirmation_id, None) is not None

    async def execute(self, request: ToolRequest) -> ToolExecutionResult:
        started_at = datetime.now(tz=timezone.utc)
        started = perf_counter()
        tool = self._registry.get(request.name)
        if tool is None:
            result = _result(
                request.name,
                ToolExecutionStatus.VALIDATION_ERROR,
                False,
                "Tool is not registered.",
                started_at,
                started,
                failure_kind=ToolFailureKind.VALIDATION,
                validation_errors=(
                    ToolValidationError(field="name", message="Unknown tool."),
                ),
            )
            self._record_history(request.name, request.arguments, None, result)
            return result

        validation = tool.input_schema.validate(request.arguments)
        if not validation.is_valid:
            result = _result(
                request.name,
                ToolExecutionStatus.VALIDATION_ERROR,
                False,
                "Tool input validation failed.",
                started_at,
                started,
                failure_kind=ToolFailureKind.VALIDATION,
                validation_errors=validation.errors,
            )
            self._log("tool_validation_failed", request.name, result)
            self._record_history(request.name, request.arguments, tool, result)
            return result

        validated_input = dict(validation.values)
        decision = await self._run_pre_execution_hook(request, tool, validated_input)
        if decision.status is not PreExecutionStatus.ALLOW:
            return self._decision_result(
                request.name,
                validated_input,
                tool,
                decision,
                started_at,
                started,
            )

        decision = await self._run_security_policy(request, tool, validated_input)
        if decision.status is not PreExecutionStatus.ALLOW:
            return self._decision_result(
                request.name,
                validated_input,
                tool,
                decision,
                started_at,
                started,
            )

        self._log("tool_execution_started", request.name)
        try:
            outcome = await asyncio.wait_for(
                tool.execute(validated_input, self._context),
                timeout=tool.timeout_seconds,
            )
            result = _result_from_outcome(request.name, outcome, started_at, started)
            self._log("tool_execution_finished", request.name, result)
            self._record_history(request.name, validated_input, tool, result)
            return result
        except TimeoutError:
            result = _result(
                request.name,
                ToolExecutionStatus.TIMEOUT,
                False,
                "Tool execution timed out.",
                started_at,
                started,
                failure_kind=ToolFailureKind.TIMEOUT,
                retryable=True,
                error_code="tool_timeout",
            )
            self._log("tool_execution_timed_out", request.name, result)
            self._record_history(request.name, validated_input, tool, result)
            return result
        except Exception as exc:
            result = _result(
                request.name,
                ToolExecutionStatus.FAILED,
                False,
                str(exc),
                started_at,
                started,
                failure_kind=ToolFailureKind.INTERNAL,
                retryable=False,
                error_code="tool_internal_error",
            )
            self._log("tool_execution_failed", request.name, result)
            self._record_history(request.name, validated_input, tool, result)
            return result

    async def _run_pre_execution_hook(
        self,
        request: ToolRequest,
        tool: BaseTool,
        validated_input: dict[str, object],
    ) -> PreExecutionDecision:
        if self._pre_execution_hook is None:
            return PreExecutionDecision.allow()
        decision = self._pre_execution_hook(request, tool, validated_input)
        if inspect.isawaitable(decision):
            return await decision
        return decision

    async def _run_security_policy(
        self,
        request: ToolRequest,
        tool: BaseTool,
        validated_input: dict[str, object],
    ) -> PreExecutionDecision:
        sanitized_request = ToolRequest(name=request.name, arguments=validated_input)
        signature = build_security_request_signature(
            sanitized_request.name,
            sanitized_request.arguments,
        )
        observation = await self._security_observation(sanitized_request.name)
        decision = self._security_policy.evaluate(
            sanitized_request,
            SecurityEvaluationContext(
                tool_description=tool.description,
                validated_arguments=validated_input,
                observation=observation,
                sensitive_fields=frozenset(tool.input_schema.sensitive_field_names()),
                is_confirmed=signature in self._confirmed_signatures,
            ),
        )
        if decision.allowed:
            self._confirmed_signatures.discard(signature)
            self._log_security_decision("security_decision_allowed", request.name, decision)
            return PreExecutionDecision.allow()
        data = _security_decision_data(decision)
        if decision.confirmation is not None:
            self._pending_confirmations[decision.confirmation.confirmation_id] = (
                decision.confirmation
            )
        self._log_security_decision("security_decision_blocked", request.name, decision)
        if decision.requires_confirmation:
            return PreExecutionDecision.pause(
                decision.confirmation.message_ru if decision.confirmation else decision.reason,
                data=data,
                error_code="security_confirmation_required",
            )
        return PreExecutionDecision.block(
            decision.reason,
            data=data,
            error_code="security_blocked",
        )

    async def _security_observation(self, tool_name: str):
        if tool_name not in {"browser.click", "browser.fill", "browser.press_key"}:
            return None
        if self._context.observation_engine is None:
            return None
        try:
            return await self._context.observation_engine.observe()
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            logger.info(
                "security_observation_failed",
                extra={
                    "event": "security_observation_failed",
                    "tool_name": tool_name,
                    "error": str(exc),
                },
            )
            return None

    def _decision_result(
        self,
        tool_name: str,
        validated_input: dict[str, object],
        tool: BaseTool,
        decision: PreExecutionDecision,
        started_at: datetime,
        started: float,
    ) -> ToolExecutionResult:
        status = (
            ToolExecutionStatus.BLOCKED
            if decision.status is PreExecutionStatus.BLOCK
            else ToolExecutionStatus.PAUSED
        )
        result = _result(
            tool_name,
            status,
            False,
            decision.reason or "Tool execution was not allowed.",
            started_at,
            started,
            data=dict(decision.data),
            failure_kind=ToolFailureKind.SECURITY,
            retryable=decision.status is PreExecutionStatus.PAUSE,
            error_code=decision.error_code,
        )
        self._log("tool_execution_blocked", tool_name, result)
        self._record_history(tool_name, validated_input, tool, result)
        return result

    def _record_history(
        self,
        tool_name: str,
        arguments: object,
        tool: BaseTool | None,
        result: ToolExecutionResult,
    ) -> None:
        redacted = _redact_arguments(arguments, tool)
        self._history.append(
            ToolHistoryEntry(
                tool_name=tool_name,
                arguments=redacted,
                result=result,
            )
        )
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

    @staticmethod
    def _log(
        event: str,
        tool_name: str,
        result: ToolExecutionResult | None = None,
    ) -> None:
        extra = {"event": event, "tool_name": tool_name}
        if result is not None:
            extra.update(
                {
                    "status": result.status.value,
                    "success": result.success,
                    "retryable": result.retryable,
                    "error_code": result.error_code,
                }
            )
        logger.info(event, extra=extra)

    @staticmethod
    def _log_security_decision(
        event: str,
        tool_name: str,
        decision: SecurityDecision,
    ) -> None:
        logger.info(
            event,
            extra={
                "event": event,
                "tool_name": tool_name,
                "risk": decision.risk.value,
                "allowed": decision.allowed,
                "requires_confirmation": decision.requires_confirmation,
                "blocked": decision.blocked,
                "audit_id": decision.audit_id,
            },
        )


def _result_from_outcome(
    tool_name: str,
    outcome: ToolExecutionOutcome,
    started_at: datetime,
    started: float,
) -> ToolExecutionResult:
    if outcome.success:
        return _result(
            tool_name,
            ToolExecutionStatus.SUCCESS,
            True,
            outcome.message,
            started_at,
            started,
            data=outcome.data,
            error_code=outcome.error_code,
        )
    return _result(
        tool_name,
        ToolExecutionStatus.FAILED,
        False,
        outcome.message,
        started_at,
        started,
        data=outcome.data,
        failure_kind=outcome.failure_kind or ToolFailureKind.BROWSER,
        retryable=outcome.retryable,
        error_code=outcome.error_code,
    )


def _result(
    tool_name: str,
    status: ToolExecutionStatus,
    success: bool,
    message: str,
    started_at: datetime,
    started: float,
    data: dict[str, object] | None = None,
    failure_kind: ToolFailureKind | None = None,
    retryable: bool = False,
    validation_errors: tuple[ToolValidationError, ...] = (),
    error_code: str | None = None,
) -> ToolExecutionResult:
    finished_at = datetime.now(tz=timezone.utc)
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status,
        success=success,
        message=message,
        data=data or {},
        failure_kind=failure_kind,
        retryable=retryable,
        validation_errors=validation_errors,
        error_code=error_code,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=(perf_counter() - started) * 1000,
    )


def _security_decision_data(decision: SecurityDecision) -> dict[str, object]:
    return {
        "security": {
            "risk": decision.risk.value,
            "reason": decision.reason,
            "audit_id": decision.audit_id,
            "classification": dict(decision.classification.to_dict()),
        },
        "confirmation": (
            dict(decision.confirmation.to_dict()) if decision.confirmation is not None else None
        ),
    }


def _redact_arguments(arguments: object, tool: BaseTool | None) -> dict[str, object]:
    if not isinstance(arguments, dict):
        return {}
    sensitive_fields = tool.input_schema.sensitive_field_names() if tool is not None else set()
    redacted: dict[str, object] = {}
    for key, value in arguments.items():
        redacted[key] = "[REDACTED]" if key in sensitive_fields else value
    return redacted
