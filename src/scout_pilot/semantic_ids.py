"""Stable semantic identifiers shared by observation and browser actions."""

from __future__ import annotations

from hashlib import sha1


def stable_semantic_id(prefix: str, *parts: object) -> str:
    """Build a stable generated ID from semantic element properties."""

    seed = "|".join(normalize_semantic_text(str(part)) for part in parts if part is not None)
    digest = sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def semantic_dedupe_key(*parts: object) -> str:
    """Build a normalized deduplication key."""

    return "|".join(normalize_semantic_text(str(part)) for part in parts if part is not None)


def normalize_semantic_text(text: str) -> str:
    """Normalize user-visible text for stable comparisons."""

    return " ".join(text.casefold().split())


def truncate_semantic_text(text: str, limit: int) -> str:
    """Compress visible text without changing its meaning."""

    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 1, 0)]}..."


def truncate_optional_semantic_text(text: str | None, limit: int) -> str | None:
    """Compress optional visible text."""

    if text is None:
        return None
    truncated = truncate_semantic_text(text, limit)
    return truncated or None
