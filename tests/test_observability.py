from __future__ import annotations

import sys
from types import ModuleType

from src.llm.observability import emit_llm_call, flush_observability, langfuse_enabled
from src.llm.types import LLMCall


class FakeObservation:
    def __init__(self, client, kwargs):
        self.client = client
        self.kwargs = kwargs
        self.update_payload = None
        self.trace_payload = None

    def __enter__(self):
        self.client.observations.append(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, **kwargs):
        self.update_payload = kwargs

    def update_trace(self, **kwargs):
        self.trace_payload = kwargs


class FakeLangfuseClient:
    def __init__(self):
        self.observations = []
        self.flushed = False

    def start_as_current_observation(self, **kwargs):
        return FakeObservation(self, kwargs)

    def flush(self):
        self.flushed = True


def test_langfuse_enabled_requires_public_and_secret(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert not langfuse_enabled()

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    assert langfuse_enabled()


def test_emit_llm_call_sends_generation_to_langfuse(monkeypatch):
    fake_client = FakeLangfuseClient()
    fake_module = ModuleType("langfuse")
    fake_module.get_client = lambda: fake_client
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.langfuse.test")

    import src.llm.observability as observability
    observability._get_langfuse_client.cache_clear()

    call = LLMCall(
        id=7,
        experiment_id=42,
        call_type="generation",
        app="mantisbt",
        class_name="AddNewProject",
        method_name="addNewProject",
        treatment="D",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system prompt",
        user_prompt="user prompt",
        raw_response="raw response",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cached_input_tokens=5,
        cost_usd=0.0012,
        latency_ms=345,
    )

    emit_llm_call(call)
    flush_observability()

    assert len(fake_client.observations) == 1
    observation = fake_client.observations[0]
    assert observation.kwargs["as_type"] == "generation"
    assert observation.kwargs["name"] == "generation · gpt-4o-mini"
    assert observation.kwargs["model"] == "gpt-4o-mini"
    assert observation.kwargs["input"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]
    assert observation.update_payload["output"] == "raw response"
    assert observation.update_payload["usage_details"] == {
        "input": 100,
        "output": 20,
        "total": 120,
        "cached_input_tokens": 5,
    }
    assert observation.update_payload["cost_details"] == {"total": 0.0012}
    assert observation.update_payload["metadata"]["local_call_id"] == 7
    assert observation.update_payload["metadata"]["experiment_id"] == 42
    assert observation.update_payload["metadata"]["treatment"] == "D"
    assert observation.trace_payload["name"] == "mantisbt · AddNewProject · TD"
    assert set(observation.trace_payload["tags"]) == {
        "app:mantisbt",
        "treatment:D",
        "model:gpt-4o-mini",
        "call_type:generation",
    }
    assert fake_client.flushed
    observability._get_langfuse_client.cache_clear()
