from __future__ import annotations

from src.llm.types import LLMResponse
from src.models import TestRecord
from src.runner import _build_generation_call


def test_build_generation_call_copies_prompt_response_and_usage_metadata():
    record = TestRecord(
        app="mantisbt",
        variant="v1",
        version="1.0",
        file_path="AddNewProject.java",
        class_name="AddNewProject",
        method_name="addNewProject",
    )
    response = LLMResponse(
        text='assertEquals("x", page.value());',
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cached_input_tokens=10,
        cache_creation_input_tokens=3,
        cache_read_input_tokens=7,
        reasoning_tokens=5,
        cost_usd=0.0012,
        latency_ms=345,
    )

    call = _build_generation_call(
        record=record,
        treatment="D",
        model_name="gpt-4o-mini",
        system="system prompt",
        user="user prompt",
        response=response,
        experiment_id=42,
    )

    assert call.experiment_id == 42
    assert call.call_type == "generation"
    assert call.app == "mantisbt"
    assert call.class_name == "AddNewProject"
    assert call.method_name == "addNewProject"
    assert call.treatment == "D"
    assert call.model == "gpt-4o-mini"
    assert call.provider == "openai"
    assert call.system_prompt == "system prompt"
    assert call.user_prompt == "user prompt"
    assert call.raw_response == 'assertEquals("x", page.value());'
    assert call.input_tokens == 100
    assert call.output_tokens == 20
    assert call.total_tokens == 120
    assert call.cached_input_tokens == 10
    assert call.cache_creation_input_tokens == 3
    assert call.cache_read_input_tokens == 7
    assert call.reasoning_tokens == 5
    assert call.cost_usd == 0.0012
    assert call.latency_ms == 345
