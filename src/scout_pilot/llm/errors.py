"""Provider error normalization."""

from __future__ import annotations

from scout_pilot.llm.types import LlmErrorCode, LlmProviderError, LlmProviderResult


def provider_error_result(
    code: LlmErrorCode,
    message: str,
    retryable: bool,
) -> LlmProviderResult:
    return LlmProviderResult(
        success=False,
        error=LlmProviderError(code=code, message=message, retryable=retryable),
    )


def malformed_response(message: str) -> LlmProviderResult:
    return provider_error_result(
        code=LlmErrorCode.MALFORMED_RESPONSE,
        message=message,
        retryable=False,
    )
