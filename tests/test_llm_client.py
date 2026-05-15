from __future__ import annotations

from types import SimpleNamespace

from src.llm.client import (
    AnthropicClient,
    CachedClient,
    GeminiClient,
    OpenAIClient,
    RetryClient,
    _getattr_path,
    _sum_present,
)
from src.llm.types import LLMResponse


class FakeClient:
    def __init__(self):
        self.calls = 0

    def generate(self, system: str, user: str) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=f"{system}:{user}",
            provider="fake",
            model="fake-model",
            input_tokens=3,
            output_tokens=5,
            total_tokens=8,
            latency_ms=12,
        )


def test_retry_client_returns_structured_response():
    client = RetryClient(FakeClient(), max_retries=1)

    response = client.generate("system", "user")

    assert response.text == "system:user"
    assert response.provider == "fake"
    assert response.model == "fake-model"
    assert response.total_tokens == 8


def test_cached_client_preserves_structured_response(tmp_path):
    inner = FakeClient()
    client = CachedClient(inner, tmp_path)

    first = client.generate("system", "user")
    second = client.generate("system", "user")

    assert inner.calls == 1
    assert first.text == second.text
    assert second.provider == "fake"
    assert second.model == "fake-model"
    assert second.total_tokens == 8


def test_getattr_path_returns_nested_provider_usage():
    class Details:
        cached_tokens = 7

    class Usage:
        prompt_tokens_details = Details()

    class Response:
        usage = Usage()

    assert _getattr_path(Response(), "usage", "prompt_tokens_details", "cached_tokens") == 7
    assert _getattr_path(Response(), "usage", "missing", "cached_tokens") is None


def test_sum_present_ignores_missing_usage_parts():
    assert _sum_present(None, None) is None
    assert _sum_present(10, None, 4) == 14


def test_openai_client_maps_usage_metadata():
    sdk_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="assertTrue(ok);"))],
                    usage=SimpleNamespace(
                        prompt_tokens=11,
                        completion_tokens=13,
                        total_tokens=24,
                        prompt_tokens_details=SimpleNamespace(cached_tokens=5),
                        completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
                    ),
                )
            )
        )
    )
    client = OpenAIClient.__new__(OpenAIClient)
    client.client = sdk_client
    client.model_id = "gpt-test"
    client.temperature = 0
    client.max_tokens = 100

    response = client.generate("system", "user")

    assert response.text == "assertTrue(ok);"
    assert response.provider == "openai"
    assert response.model == "gpt-test"
    assert response.input_tokens == 11
    assert response.output_tokens == 13
    assert response.total_tokens == 24
    assert response.cached_input_tokens == 5
    assert response.reasoning_tokens == 3
    assert response.latency_ms is not None


def test_anthropic_client_maps_usage_metadata():
    sdk_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **_: SimpleNamespace(
                content=[SimpleNamespace(text="assertTrue(ok);")],
                usage=SimpleNamespace(
                    input_tokens=17,
                    output_tokens=19,
                    cache_creation_input_tokens=2,
                    cache_read_input_tokens=7,
                ),
            )
        )
    )
    client = AnthropicClient.__new__(AnthropicClient)
    client.client = sdk_client
    client.model_id = "claude-test"
    client.temperature = 0
    client.max_tokens = 100

    response = client.generate("system", "user")

    assert response.text == "assertTrue(ok);"
    assert response.provider == "anthropic"
    assert response.model == "claude-test"
    assert response.input_tokens == 17
    assert response.output_tokens == 19
    assert response.total_tokens == 36
    assert response.cached_input_tokens == 9
    assert response.latency_ms is not None


def test_gemini_client_maps_usage_metadata():
    sdk_client = SimpleNamespace(
        generate_content=lambda *_, **__: SimpleNamespace(
            text="assertTrue(ok);",
            usage_metadata=SimpleNamespace(
                prompt_token_count=23,
                candidates_token_count=29,
                total_token_count=52,
                cached_content_token_count=4,
                thoughts_token_count=6,
            ),
        )
    )
    client = GeminiClient.__new__(GeminiClient)
    client.client = sdk_client
    client.model_id = "gemini-test"
    client.temperature = 0
    client.max_tokens = 100

    response = client.generate("system", "user")

    assert response.text == "assertTrue(ok);"
    assert response.provider == "gemini"
    assert response.model == "gemini-test"
    assert response.input_tokens == 23
    assert response.output_tokens == 29
    assert response.total_tokens == 52
    assert response.cached_input_tokens == 4
    assert response.reasoning_tokens == 6
    assert response.latency_ms is not None
