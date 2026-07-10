import asyncio
from types import SimpleNamespace

from scout_pilot.llm import (
    AnthropicToolSchemaAdapter,
    LlmErrorCode,
    LlmFinishReason,
    LlmMessage,
    LlmMessageRole,
    LlmProviderConfig,
    LlmProviderName,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
    MockLlmProvider,
    OpenAIToolSchemaAdapter,
    ReasoningContext,
    ReasoningEngine,
    ReasoningStatus,
    create_llm_provider,
)
from scout_pilot.llm.anthropic_provider import AnthropicLlmProvider
from scout_pilot.llm.openai_provider import OpenAILlmProvider
from scout_pilot.llm.types import LlmProviderError
from scout_pilot.models import PageObservation
from scout_pilot.tools import create_browser_tool_registry


def test_tool_schema_adapters_convert_without_provider_methods_on_tools():
    schemas = create_browser_tool_registry().schemas()
    navigate_schema = next(schema for schema in schemas if schema.name == "browser.navigate")

    openai_tool = OpenAIToolSchemaAdapter().convert_tool(navigate_schema)
    anthropic_tool = AnthropicToolSchemaAdapter().convert_tool(navigate_schema)

    assert openai_tool["type"] == "function"
    assert openai_tool["function"]["name"] == "browser.navigate"
    assert openai_tool["function"]["parameters"]["properties"]["url"]["type"] == "string"
    assert anthropic_tool["name"] == "browser.navigate"
    assert anthropic_tool["input_schema"]["required"] == ["url"]


def test_provider_factory_selects_configured_adapter(monkeypatch):
    created = []

    def fake_openai_init(self, config, client=None, tool_adapter=None):
        created.append(("openai", config.model))

    def fake_anthropic_init(self, config, client=None, tool_adapter=None):
        created.append(("anthropic", config.model))

    monkeypatch.setattr(OpenAILlmProvider, "__init__", fake_openai_init)
    monkeypatch.setattr(AnthropicLlmProvider, "__init__", fake_anthropic_init)

    openai_provider = create_llm_provider(_config(LlmProviderName.OPENAI))
    anthropic_provider = create_llm_provider(_config(LlmProviderName.ANTHROPIC))

    assert isinstance(openai_provider, OpenAILlmProvider)
    assert isinstance(anthropic_provider, AnthropicLlmProvider)
    assert created == [("openai", "test-model"), ("anthropic", "test-model")]


def test_openai_provider_parses_tool_call_response():
    client = FakeOpenAIClient(
        response=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="browser.navigate",
                                    arguments='{"url":"https://example.test"}',
                                )
                            )
                        ],
                    ),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
    )
    provider = OpenAILlmProvider(_config(LlmProviderName.OPENAI), client=client)

    result = asyncio.run(provider.complete(_request()))

    assert result.success is True
    assert result.response is not None
    assert result.response.finish_reason is LlmFinishReason.TOOL_CALLS
    assert result.response.tool_calls[0].name == "browser.navigate"
    assert result.response.tool_calls[0].arguments == {"url": "https://example.test"}
    assert result.response.usage.total_tokens == 15
    assert client.kwargs["tools"][0]["function"]["name"] == "browser.navigate"


def test_openai_provider_normalizes_malformed_response_and_rate_limit():
    malformed = OpenAILlmProvider(
        _config(LlmProviderName.OPENAI),
        client=FakeOpenAIClient(response=SimpleNamespace(choices=[])),
    )
    malformed_result = asyncio.run(malformed.complete(_request()))

    rate_limited = OpenAILlmProvider(
        _config(LlmProviderName.OPENAI),
        client=FakeOpenAIClient(error=FakeRateLimitError("Too many requests")),
    )
    rate_result = asyncio.run(rate_limited.complete(_request()))

    assert malformed_result.success is False
    assert malformed_result.error.code is LlmErrorCode.MALFORMED_RESPONSE
    assert rate_result.success is False
    assert rate_result.error.code is LlmErrorCode.RATE_LIMIT
    assert rate_result.error.retryable is True


def test_anthropic_provider_parses_text_and_tool_use_response():
    client = FakeAnthropicClient(
        response=SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Working."),
                SimpleNamespace(type="tool_use", name="browser.click", input={"element_id": "el_1"}),
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )
    )
    provider = AnthropicLlmProvider(_config(LlmProviderName.ANTHROPIC), client=client)

    result = asyncio.run(provider.complete(_request()))

    assert result.success is True
    assert result.response.content == "Working."
    assert result.response.tool_calls[0] == LlmToolCall(
        name="browser.click",
        arguments={"element_id": "el_1"},
    )
    assert result.response.usage.total_tokens == 10
    assert client.kwargs["tools"][0]["name"] == "browser.navigate"


def test_anthropic_provider_normalizes_invalid_credentials():
    provider = AnthropicLlmProvider(
        _config(LlmProviderName.ANTHROPIC),
        client=FakeAnthropicClient(error=FakeAuthenticationError("Bad key")),
    )

    result = asyncio.run(provider.complete(_request()))

    assert result.success is False
    assert result.error.code is LlmErrorCode.INVALID_CREDENTIALS
    assert result.error.retryable is False


def test_reasoning_engine_selects_tool_answer_and_special_states():
    schemas = create_browser_tool_registry().schemas()
    provider = MockLlmProvider(
        [
            LlmProviderResult(
                success=True,
                response=LlmProviderResponse(
                    tool_calls=(
                        LlmToolCall(
                            name="browser.observe",
                            arguments={},
                        ),
                    )
                ),
            ),
            LlmProviderResult(success=True, response=LlmProviderResponse(content="Done.")),
            LlmProviderResult(
                success=True,
                response=LlmProviderResponse(content="NEED_OBSERVATION: page changed"),
            ),
            LlmProviderResult(
                success=True,
                response=LlmProviderResponse(content="NEED_CONFIRMATION: submit form"),
            ),
        ]
    )
    engine = ReasoningEngine(provider)
    context = ReasoningContext(
        user_task="Find a vacancy",
        observation=PageObservation(
            url="https://example.test",
            title="Example",
            summary="Compact page.",
        ),
        available_tools=schemas,
        security_constraints=["Do not submit forms without approval."],
        confirmation_constraints=["Ask before external side effects."],
        budget={"remaining_tokens": 1000},
    )

    tool_result = asyncio.run(engine.reason(context))
    answer_result = asyncio.run(engine.reason(context))
    observation_result = asyncio.run(engine.reason(context))
    confirmation_result = asyncio.run(engine.reason(context))

    assert tool_result.status is ReasoningStatus.TOOL_SELECTED
    assert tool_result.selected_tool.name == "browser.observe"
    assert answer_result.status is ReasoningStatus.ANSWER
    assert answer_result.answer == "Done."
    assert observation_result.status is ReasoningStatus.NEEDS_OBSERVATION
    assert confirmation_result.status is ReasoningStatus.NEEDS_CONFIRMATION
    assert "raw HTML" in provider.requests[0].messages[0].content
    assert "Compact page" in provider.requests[0].messages[1].content


def test_reasoning_engine_handles_provider_failure_and_unknown_tool():
    schemas = create_browser_tool_registry().schemas()
    failure_engine = ReasoningEngine(
        MockLlmProvider(
            [
                LlmProviderResult(
                    success=False,
                    error=LlmProviderError(
                        code=LlmErrorCode.TIMEOUT,
                        message="Timed out.",
                        retryable=True,
                    ),
                )
            ]
        )
    )
    unknown_tool_engine = ReasoningEngine(
        MockLlmProvider(
            [
                LlmProviderResult(
                    success=True,
                    response=LlmProviderResponse(
                        tool_calls=(LlmToolCall(name="browser.unknown", arguments={}),)
                    ),
                )
            ]
        )
    )
    context = ReasoningContext(
        user_task="Act",
        observation=None,
        available_tools=schemas,
    )

    provider_failure = asyncio.run(failure_engine.reason(context))
    unknown_tool = asyncio.run(unknown_tool_engine.reason(context))

    assert provider_failure.status is ReasoningStatus.FAILURE
    assert provider_failure.provider_error.code is LlmErrorCode.TIMEOUT
    assert unknown_tool.status is ReasoningStatus.FAILURE
    assert "unknown tool" in unknown_tool.message.lower()


def _config(provider: LlmProviderName) -> LlmProviderConfig:
    return LlmProviderConfig(
        provider=provider,
        model="test-model",
        timeout_seconds=3,
        max_output_tokens=200,
        api_key="test-key",
    )


def _request() -> LlmProviderRequest:
    return LlmProviderRequest(
        messages=[
            LlmMessage(role=LlmMessageRole.SYSTEM, content="System"),
            LlmMessage(role=LlmMessageRole.USER, content="User"),
        ],
        tools=create_browser_tool_registry().schemas(),
        model="test-model",
        max_output_tokens=100,
        timeout_seconds=2,
    )


class FakeRateLimitError(Exception):
    pass


FakeRateLimitError.__name__ = "RateLimitError"


class FakeAuthenticationError(Exception):
    pass


FakeAuthenticationError.__name__ = "AuthenticationError"


class FakeOpenAIClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = {}
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response
