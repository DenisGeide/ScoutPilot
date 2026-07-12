"""Manual live LLM provider smoke checks for the CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scout_pilot.context import DeterministicContextBudgeter
from scout_pilot.config import AppConfig
from scout_pilot.llm import (
    LlmErrorCode,
    LlmFinishReason,
    LlmMessage,
    LlmMessageRole,
    LlmProviderConfig,
    LlmProviderName,
    LlmProviderRequest,
    LlmProviderResult,
    create_llm_provider,
)
from scout_pilot.llm.provider import LlmProvider


ProviderFactory = Callable[[LlmProviderConfig], LlmProvider]


@dataclass(frozen=True)
class ProviderSmokeSettings:
    """Settings for one manual provider smoke check."""

    provider: str
    env_file: Path | None = Path(".env")

    def __post_init__(self) -> None:
        if self.provider not in {"openai", "anthropic", "codex"}:
            raise ValueError("provider must be 'openai', 'anthropic' or 'codex'")


@dataclass(frozen=True)
class ProviderSmokeResult:
    """User-facing result of a provider smoke check."""

    success: bool
    message_ru: str
    exit_code: int
    failure_code: LlmErrorCode | str | None = None


async def run_provider_smoke(
    settings: ProviderSmokeSettings,
    *,
    provider_factory: ProviderFactory = create_llm_provider,
) -> ProviderSmokeResult:
    """Send a tiny provider-neutral request to a live provider adapter."""

    config = AppConfig.load(env_file=settings.env_file)
    provider_name = LlmProviderName(settings.provider)
    api_key = _api_key_for_provider(config, provider_name)
    if provider_name is not LlmProviderName.CODEX and not api_key:
        return ProviderSmokeResult(
            success=False,
            exit_code=1,
            failure_code="missing_key",
            message_ru=(
                f"Не настроен API-ключ для провайдера {provider_name.value}. "
                "Добавьте его только в локальный .env и повторите provider-smoke. "
                "Автоматические тесты live-провайдеров не вызывают."
            ),
        )

    provider_config = LlmProviderConfig(
        provider=provider_name,
        model=config.llm_model,
        timeout_seconds=config.llm_timeout_seconds,
        max_output_tokens=min(config.llm_max_output_tokens, 64),
        api_key=api_key,
    )
    try:
        provider = provider_factory(provider_config)
        result = await provider.complete(_smoke_request(provider_config))
    except TimeoutError:
        return _failure(provider_name, LlmErrorCode.TIMEOUT)
    except Exception as exc:
        if _is_missing_provider_sdk(exc):
            return _failure(provider_name, LlmErrorCode.CONFIGURATION_ERROR)
        return _failure(provider_name, LlmErrorCode.UNKNOWN)

    if not result.success:
        code = result.error.code if result.error is not None else LlmErrorCode.UNKNOWN
        return _failure(provider_name, code)

    if (
        result.response is None
        or result.response.finish_reason is LlmFinishReason.LENGTH
        or not (result.response.content or "").strip()
    ):
        return _failure(provider_name, LlmErrorCode.MALFORMED_RESPONSE)

    answer = _safe_snippet(result.response.content or "")
    return ProviderSmokeResult(
        success=True,
        exit_code=0,
        message_ru=(
            f"Проверка {provider_name.value} прошла: провайдер ответил на короткий "
            "безопасный запрос. Браузер, HTML, куки, токены и приватные файлы "
            f"не использовались. Ответ: {answer}"
        ),
    )


def _api_key_for_provider(config: AppConfig, provider: LlmProviderName) -> str | None:
    if provider is LlmProviderName.OPENAI:
        return config.provider_secrets.openai_api_key
    if provider is LlmProviderName.ANTHROPIC:
        return config.provider_secrets.anthropic_api_key
    return None


def _smoke_request(config: LlmProviderConfig) -> LlmProviderRequest:
    budgeted = DeterministicContextBudgeter().assemble(
        user_task="provider smoke connectivity check",
        observation=None,
        memory_summaries=(),
        max_input_tokens=512,
        reserved_output_tokens=config.max_output_tokens,
    )
    payload = {
        "task": "Ответь одной короткой фразой: провайдер доступен, тестовый запрос получен.",
        "context_metrics": dict(budgeted.metrics.to_dict()),
        "budget": dict(budgeted.budget),
    }
    return LlmProviderRequest(
        messages=(
            LlmMessage(
                role=LlmMessageRole.SYSTEM,
                content=(
                    "You are a tiny Scout Pilot provider connectivity smoke test. "
                    "Use only this synthetic connectivity prompt. Do not ask for external "
                    "context, tools, files, sessions, secrets, page data, or account data. "
                    "The user payload includes safe context budget metrics for traceability. "
                    "Reply with one short Russian sentence that confirms the provider works."
                ),
            ),
            LlmMessage(
                role=LlmMessageRole.USER,
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        ),
        tools=(),
        model=config.model,
        max_output_tokens=config.max_output_tokens,
        timeout_seconds=config.timeout_seconds,
    )


def _failure(provider: LlmProviderName, code: LlmErrorCode) -> ProviderSmokeResult:
    messages = {
        LlmErrorCode.INVALID_CREDENTIALS: (
            f"Провайдер {provider.value} отклонил API-ключ. Проверьте значение в локальном "
            ".env, не коммитьте ключ и повторите smoke-проверку."
        ),
        LlmErrorCode.TIMEOUT: (
            f"Провайдер {provider.value} не ответил за отведенный таймаут. Проверьте сеть, "
            "таймаут в .env и повторите позже."
        ),
        LlmErrorCode.RATE_LIMIT: (
            f"Провайдер {provider.value} вернул лимит запросов. Подождите, проверьте лимиты "
            "аккаунта и повторите smoke-проверку."
        ),
        LlmErrorCode.MALFORMED_RESPONSE: (
            f"Провайдер {provider.value} ответил в неожиданном формате. Интеграция дошла "
            "до провайдера, но ответ нельзя надежно использовать."
        ),
        LlmErrorCode.PROVIDER_UNAVAILABLE: (
            f"Провайдер {provider.value} сейчас недоступен или сеть не отвечает. "
            "Повторите проверку позже."
        ),
        LlmErrorCode.CONFIGURATION_ERROR: (
            f"Конфигурация провайдера {provider.value} некорректна. Проверьте .env, "
            "модель и установку optional dependencies: `python -m pip install -e \".[providers]\"`."
        ),
        LlmErrorCode.UNKNOWN: (
            f"Проверка провайдера {provider.value} остановилась из-за неизвестной ошибки. "
            "Ключи и приватные данные не выводятся; проверьте .env, модель, сеть и зависимости."
        ),
    }
    return ProviderSmokeResult(
        success=False,
        exit_code=1,
        failure_code=code,
        message_ru=messages.get(code, messages[LlmErrorCode.UNKNOWN]),
    )


def _safe_snippet(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:180] if normalized else "ответ без текста"


def _is_missing_provider_sdk(exc: Exception) -> bool:
    message = str(exc).casefold()
    return (
        "sdk is not installed" in message
        or "no module named 'openai'" in message
        or "no module named 'anthropic'" in message
    )
