"""Provider-neutral tool contracts and schemas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ToolValueType(str, Enum):
    """JSON-like provider-neutral value types."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class ToolExecutionStatus(str, Enum):
    """Runtime status for one tool execution."""

    SUCCESS = "success"
    VALIDATION_ERROR = "validation_error"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    PAUSED = "paused"


class ToolFailureKind(str, Enum):
    """Failure category used by retry and recovery logic."""

    VALIDATION = "validation"
    TIMEOUT = "timeout"
    BROWSER = "browser"
    SECURITY = "security"
    INTERNAL = "internal"


class PreExecutionStatus(str, Enum):
    """Security hook decision before a tool touches the browser."""

    ALLOW = "allow"
    BLOCK = "block"
    PAUSE = "pause"


@dataclass(frozen=True)
class ToolFieldSchema:
    """One provider-neutral input or output field."""

    name: str
    value_type: ToolValueType
    description: str
    required: bool = True
    sensitive: bool = False
    default: Any | None = None
    min_length: int | None = None
    max_length: int | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    enum_values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ToolValidationError:
    """Validation error for one tool input field."""

    field: str
    message: str


@dataclass(frozen=True)
class ToolValidationResult:
    """Validated input data or validation errors."""

    values: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[ToolValidationError, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ToolInputSchema:
    """Provider-neutral input schema."""

    fields: tuple[ToolFieldSchema, ...] = ()
    allow_extra: bool = False

    def validate(self, arguments: Mapping[str, Any]) -> ToolValidationResult:
        values: dict[str, Any] = {}
        errors: list[ToolValidationError] = []
        fields_by_name = {field.name: field for field in self.fields}

        for field_schema in self.fields:
            if field_schema.name not in arguments:
                if field_schema.required and field_schema.default is None:
                    errors.append(
                        ToolValidationError(
                            field=field_schema.name,
                            message="Required field is missing.",
                        )
                    )
                    continue
                values[field_schema.name] = field_schema.default
                continue

            value = arguments[field_schema.name]
            field_errors = _validate_field(field_schema, value)
            if field_errors:
                errors.extend(field_errors)
                continue
            values[field_schema.name] = value

        if not self.allow_extra:
            for name in arguments:
                if name not in fields_by_name:
                    errors.append(
                        ToolValidationError(
                            field=name,
                            message="Unexpected field.",
                        )
                    )

        return ToolValidationResult(values=values, errors=tuple(errors))

    def sensitive_field_names(self) -> set[str]:
        return {field.name for field in self.fields if field.sensitive}


@dataclass(frozen=True)
class ToolOutputSchema:
    """Provider-neutral output schema."""

    fields: tuple[ToolFieldSchema, ...] = ()


@dataclass(frozen=True)
class ToolSchema:
    """Complete provider-neutral schema for a registered tool."""

    name: str
    description: str
    input_schema: ToolInputSchema
    output_schema: ToolOutputSchema


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """Tool implementation output before runtime metadata is added."""

    success: bool
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)
    failure_kind: ToolFailureKind | None = None
    retryable: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    """Structured runtime result for a tool request."""

    tool_name: str
    status: ToolExecutionStatus
    success: bool
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)
    failure_kind: ToolFailureKind | None = None
    retryable: bool = False
    validation_errors: tuple[ToolValidationError, ...] = ()
    error_code: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    duration_ms: float = 0.0


@dataclass(frozen=True)
class ToolHistoryEntry:
    """Redacted tool execution history entry."""

    tool_name: str
    arguments: Mapping[str, Any]
    result: ToolExecutionResult


@dataclass(frozen=True)
class PreExecutionDecision:
    """Pre-execution hook decision."""

    status: PreExecutionStatus
    reason: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    @classmethod
    def allow(cls) -> "PreExecutionDecision":
        return cls(status=PreExecutionStatus.ALLOW)

    @classmethod
    def block(
        cls,
        reason: str,
        data: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> "PreExecutionDecision":
        return cls(
            status=PreExecutionStatus.BLOCK,
            reason=reason,
            data=dict(data or {}),
            error_code=error_code,
        )

    @classmethod
    def pause(
        cls,
        reason: str,
        data: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> "PreExecutionDecision":
        return cls(
            status=PreExecutionStatus.PAUSE,
            reason=reason,
            data=dict(data or {}),
            error_code=error_code,
        )


def _validate_field(
    field_schema: ToolFieldSchema,
    value: Any,
) -> Sequence[ToolValidationError]:
    errors: list[ToolValidationError] = []
    if not _matches_type(value, field_schema.value_type):
        return [
            ToolValidationError(
                field=field_schema.name,
                message=f"Expected {field_schema.value_type.value}.",
            )
        ]

    if field_schema.enum_values and value not in field_schema.enum_values:
        errors.append(
            ToolValidationError(
                field=field_schema.name,
                message="Value is not one of the allowed options.",
            )
        )
    if isinstance(value, str):
        if field_schema.min_length is not None and len(value) < field_schema.min_length:
            errors.append(
                ToolValidationError(
                    field=field_schema.name,
                    message=f"Minimum length is {field_schema.min_length}.",
                )
            )
        if field_schema.max_length is not None and len(value) > field_schema.max_length:
            errors.append(
                ToolValidationError(
                    field=field_schema.name,
                    message=f"Maximum length is {field_schema.max_length}.",
                )
            )
    if isinstance(value, int | float) and not isinstance(value, bool):
        if field_schema.minimum is not None and value < field_schema.minimum:
            errors.append(
                ToolValidationError(
                    field=field_schema.name,
                    message=f"Minimum value is {field_schema.minimum}.",
                )
            )
        if field_schema.maximum is not None and value > field_schema.maximum:
            errors.append(
                ToolValidationError(
                    field=field_schema.name,
                    message=f"Maximum value is {field_schema.maximum}.",
                )
            )
    return errors


def _matches_type(value: Any, value_type: ToolValueType) -> bool:
    if value_type is ToolValueType.STRING:
        return isinstance(value, str)
    if value_type is ToolValueType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is ToolValueType.NUMBER:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if value_type is ToolValueType.BOOLEAN:
        return isinstance(value, bool)
    if value_type is ToolValueType.OBJECT:
        return isinstance(value, Mapping)
    if value_type is ToolValueType.ARRAY:
        return isinstance(value, Sequence) and not isinstance(value, str | bytes)
    return False
