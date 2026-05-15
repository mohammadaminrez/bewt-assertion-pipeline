from __future__ import annotations

from src.llm.client import CachedClient, RetryClient
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
