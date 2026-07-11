import asyncio
import json

from scout_pilot.cli.provider_smoke import ProviderSmokeSettings, run_provider_smoke
from scout_pilot.llm import (
    LlmErrorCode,
    LlmProviderError,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
)


def test_provider_smoke_reports_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=\n", encoding="utf-8")

    result = asyncio.run(
        run_provider_smoke(ProviderSmokeSettings(provider="openai", env_file=env_file))
    )

    assert result.success is False
    assert result.exit_code == 1
    assert result.failure_code == "missing_key"
    assert "API-ключ" in result.message_ru
    assert ".env" in result.message_ru


def test_provider_smoke_sends_tiny_provider_neutral_request(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=unit-test-key\n", encoding="utf-8")
    fake_provider = FakeProvider(
        LlmProviderResult(
            success=True,
            response=LlmProviderResponse(content="Провайдер доступен."),
        )
    )

    result = asyncio.run(
        run_provider_smoke(
            ProviderSmokeSettings(provider="openai", env_file=env_file),
            provider_factory=lambda _config: fake_provider,
        )
    )

    request = fake_provider.requests[0]
    serialized_messages = "\n".join(message.content for message in request.messages).casefold()
    payload = json.loads(request.messages[1].content)

    assert result.success is True
    assert result.exit_code == 0
    assert "Проверка openai прошла" in result.message_ru
    assert "Провайдер доступен" in result.message_ru
    assert isinstance(request, LlmProviderRequest)
    assert request.tools == ()
    assert payload["context_metrics"]["after_tokens"] <= payload["context_metrics"]["before_tokens"]
    assert payload["context_metrics"]["observation_sections_kept"] == 0
    assert "browser state" not in serialized_messages
    assert "page html" not in serialized_messages
    assert "private file" not in serialized_messages
    assert "unit-test-key" not in serialized_messages


def test_provider_smoke_classifies_common_provider_failures(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=unit-test-key\n", encoding="utf-8")
    cases = [
        (LlmErrorCode.INVALID_CREDENTIALS, "отклонил API-ключ"),
        (LlmErrorCode.TIMEOUT, "таймаут"),
        (LlmErrorCode.RATE_LIMIT, "лимит запросов"),
        (LlmErrorCode.MALFORMED_RESPONSE, "неожиданном формате"),
    ]

    for code, expected in cases:
        fake_provider = FakeProvider(
            LlmProviderResult(
                success=False,
                error=LlmProviderError(code=code, message="hidden provider details"),
            )
        )

        result = asyncio.run(
            run_provider_smoke(
                ProviderSmokeSettings(provider="anthropic", env_file=env_file),
                provider_factory=lambda _config, provider=fake_provider: provider,
            )
        )

        assert result.success is False
        assert result.exit_code == 1
        assert result.failure_code is code
        assert expected in result.message_ru
        assert "hidden provider details" not in result.message_ru


def test_provider_smoke_treats_empty_success_as_malformed(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=unit-test-key\n", encoding="utf-8")
    fake_provider = FakeProvider(
        LlmProviderResult(
            success=True,
            response=LlmProviderResponse(content=""),
        )
    )

    result = asyncio.run(
        run_provider_smoke(
            ProviderSmokeSettings(provider="openai", env_file=env_file),
            provider_factory=lambda _config: fake_provider,
        )
    )

    assert result.success is False
    assert result.failure_code is LlmErrorCode.MALFORMED_RESPONSE
    assert "неожиданном формате" in result.message_ru


def test_provider_smoke_reports_missing_optional_provider_sdk(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=unit-test-key\n", encoding="utf-8")

    def missing_sdk_factory(_config):
        raise RuntimeError("OpenAI SDK is not installed.")

    result = asyncio.run(
        run_provider_smoke(
            ProviderSmokeSettings(provider="openai", env_file=env_file),
            provider_factory=missing_sdk_factory,
        )
    )

    assert result.success is False
    assert result.failure_code is LlmErrorCode.CONFIGURATION_ERROR
    assert "providers" in result.message_ru


class FakeProvider:
    def __init__(self, result: LlmProviderResult) -> None:
        self.result = result
        self.requests: list[LlmProviderRequest] = []

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        self.requests.append(request)
        return self.result
