"""Privacy filtering for hierarchical memory."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from scout_pilot.memory.types import MemorySettings
from scout_pilot.models import MemoryRecord


_FORBIDDEN_KEY_FRAGMENTS = (
    "api_key",
    "auth_state",
    "authentication",
    "authorization",
    "browser_profile",
    "cookie",
    "dom",
    "html",
    "password",
    "private_file",
    "private_screenshot",
    "profile_dir",
    "raw_email",
    "resume",
    "screenshot",
    "secret",
    "session",
    "token",
)
_SENSITIVE_VALUE_KEYS = (
    "field_value",
    "input_value",
    "password",
    "raw_value",
    "secret",
    "submitted_value",
    "token",
)
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"(?i)(password|token|secret|cookie)=\S+"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
)
_RAW_MARKUP_PATTERN = re.compile(
    r"(?is)<\s*(html|head|body|script|style|form|input|button|div|span|a)\b"
)
_PRIVATE_ARTIFACT_PATTERN = re.compile(
    r"(?i)(screenshot|browser-profile|\.browser-profiles|cookies?|session|auth).*\."
    r"(png|jpg|jpeg|webp|json|sqlite|db)"
)


@dataclass(frozen=True)
class MemoryPrivacyResult:
    """Privacy filtering result for one memory record."""

    accepted: bool
    record: MemoryRecord | None = None
    reason: str = ""
    redacted: bool = False


class MemoryPrivacyFilter:
    """Remove unsafe memory content before it can be stored."""

    def __init__(self, settings: MemorySettings | None = None) -> None:
        self._settings = settings or MemorySettings()

    def sanitize(self, record: MemoryRecord) -> MemoryPrivacyResult:
        if record.contains_private_data:
            return MemoryPrivacyResult(
                accepted=False,
                reason="Record is explicitly marked as private.",
            )

        sanitized, redacted = self._sanitize_mapping(record.value)
        if not sanitized:
            return MemoryPrivacyResult(
                accepted=False,
                reason="Record does not contain safe memory content.",
                redacted=redacted,
            )

        return MemoryPrivacyResult(
            accepted=True,
            record=replace(
                record,
                value=sanitized,
                contains_private_data=False,
            ),
            redacted=redacted,
        )

    def _sanitize_mapping(self, value: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        output: dict[str, Any] = {}
        redacted = False
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.casefold()
            if _is_forbidden_key(lowered):
                redacted = True
                continue
            if _is_sensitive_value_key(lowered):
                output[key_text] = "[redacted]"
                redacted = True
                continue
            sanitized_item, item_redacted, keep_item = self._sanitize_value(item)
            redacted = redacted or item_redacted
            if keep_item:
                output[key_text] = sanitized_item
        return output, redacted

    def _sanitize_value(self, value: Any) -> tuple[Any, bool, bool]:
        if isinstance(value, str):
            sanitized, redacted, keep = self._sanitize_string(value)
            return sanitized, redacted, keep
        if isinstance(value, Mapping):
            sanitized, redacted = self._sanitize_mapping(value)
            return sanitized, redacted, bool(sanitized)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            output = []
            redacted = False
            for item in value:
                sanitized, item_redacted, keep = self._sanitize_value(item)
                redacted = redacted or item_redacted
                if keep:
                    output.append(sanitized)
            return output, redacted, bool(output)
        if value is None or isinstance(value, bool | int | float):
            return value, False, True
        return str(value)[: self._settings.max_value_chars], True, True

    def _sanitize_string(self, value: str) -> tuple[str, bool, bool]:
        if _looks_like_raw_markup(value):
            return "", True, False
        if _PRIVATE_ARTIFACT_PATTERN.search(value):
            return "[redacted-private-artifact]", True, True

        sanitized = value
        redacted = False
        for pattern in _SECRET_PATTERNS:
            new_value = pattern.sub("[redacted]", sanitized)
            if new_value != sanitized:
                sanitized = new_value
                redacted = True

        if len(sanitized) > self._settings.max_value_chars:
            sanitized = sanitized[: self._settings.max_value_chars].rstrip() + "..."
            redacted = True
        return sanitized, redacted, bool(sanitized.strip())


def _is_forbidden_key(key: str) -> bool:
    return any(fragment in key for fragment in _FORBIDDEN_KEY_FRAGMENTS)


def _is_sensitive_value_key(key: str) -> bool:
    return any(fragment in key for fragment in _SENSITIVE_VALUE_KEYS)


def _looks_like_raw_markup(value: str) -> bool:
    return bool(_RAW_MARKUP_PATTERN.search(value) and value.count("<") >= 3)
