from __future__ import annotations

import sys
from types import ModuleType

import pytest

import src.llm.observability as observability
from src.llm.observability import emit_llm_call, flush_observability, posthog_enabled
from src.llm.types import LLMCall


class FakePosthog:
    instances = []

    def __init__(self, token, host=None):
        self.token = token
        self.host = host
        self.captured = []
        self.flushed = False
        FakePosthog.instances.append(self)

    def capture(self, event, **kwargs):
        self.captured.append((event, kwargs))

    def flush(self):
        self.flushed = True


@pytest.fixture
def fake_posthog(monkeypatch):
    FakePosthog.instances = []
    fake_module = ModuleType("posthog")
    fake_module.Posthog = FakePosthog
    monkeypatch.setitem(sys.modules, "posthog", fake_module)
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.test")
    observability._get_posthog_client.cache_clear()
    yield FakePosthog
    observability._get_posthog_client.cache_clear()


def _call(**overrides) -> LLMCall:
    fields = dict(
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
    fields.update(overrides)
    return LLMCall(**fields)


def test_posthog_enabled_requires_token_and_host(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    assert not posthog_enabled()

    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    assert not posthog_enabled()

    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.test")
    assert posthog_enabled()


def test_missing_config_warns_and_sends_nothing(monkeypatch):
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.test")
    observability._get_posthog_client.cache_clear()
    try:
        with pytest.warns(UserWarning, match="POSTHOG_PROJECT_TOKEN variable required by PostHog"):
            emit_llm_call(_call())
        flush_observability()  # must not raise
    finally:
        observability._get_posthog_client.cache_clear()


def test_emit_llm_call_captures_ai_generation(fake_posthog):
    emit_llm_call(_call())
    flush_observability()

    client = fake_posthog.instances[0]
    assert client.token == "phc_test"
    assert client.host == "https://eu.i.posthog.test"
    assert client.flushed

    assert len(client.captured) == 1
    event, kwargs = client.captured[0]
    assert event == "$ai_generation"
    assert kwargs["distinct_id"] == "bewt-pipeline"

    props = kwargs["properties"]
    assert props["$ai_model"] == "gpt-4o-mini"
    assert props["$ai_provider"] == "openai"
    assert props["$ai_input"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]
    assert props["$ai_output_choices"] == [{"role": "assistant", "content": "raw response"}]
    assert props["$ai_input_tokens"] == 100
    assert props["$ai_output_tokens"] == 20
    assert props["$ai_cache_read_input_tokens"] == 5
    assert props["$ai_latency"] == 0.345
    assert props["$ai_total_cost_usd"] == 0.0012
    assert props["$ai_cost_passthrough"] is True
    assert props["$ai_session_id"] == observability.RUN_ID
    assert props["$ai_trace_id"] == f"{observability.RUN_ID}:bewt:mantisbt:AddNewProject:gpt-4o-mini"
    assert props["$ai_span_name"] == "mantisbt · AddNewProject · TD · bewt"
    assert props["$process_person_profile"] is False
    assert "$ai_is_error" not in props

    assert props["local_call_id"] == 7
    assert props["experiment_id"] == 42
    assert props["treatment"] == "D"
    assert props["source"] == "bewt"
    assert props["call_type"] == "generation"


def test_treatments_of_one_test_share_a_trace(fake_posthog):
    emit_llm_call(_call(treatment="A"))
    emit_llm_call(_call(treatment="D"))
    emit_llm_call(_call(treatment="A", class_name="OtherTest"))

    trace_ids = [kw["properties"]["$ai_trace_id"] for _, kw in fake_posthog.instances[0].captured]
    assert trace_ids[0] == trace_ids[1]
    assert trace_ids[0] != trace_ids[2]


def test_anthropic_cache_tokens_are_mapped(fake_posthog):
    emit_llm_call(_call(
        provider="anthropic", model="claude-haiku",
        cached_input_tokens=30, cache_read_input_tokens=30, cache_creation_input_tokens=50,
    ))
    props = fake_posthog.instances[0].captured[0][1]["properties"]
    assert props["$ai_cache_read_input_tokens"] == 30
    assert props["$ai_cache_creation_input_tokens"] == 50


def test_failed_provider_call_is_marked_as_error(fake_posthog):
    emit_llm_call(_call(provider="", raw_response="RateLimitError: slow down",
                        input_tokens=None, output_tokens=None, cost_usd=None))
    props = fake_posthog.instances[0].captured[0][1]["properties"]
    assert props["$ai_is_error"] is True
    assert props["$ai_error"] == "RateLimitError: slow down"
    assert "$ai_total_cost_usd" not in props
