from __future__ import annotations

from src.data.store import ResultStore
from src.llm.types import LLMCall


def test_get_llm_usage_summary_grouped_by_treatment(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_llm_call(LLMCall(
        call_type="generation",
        app="mantisbt",
        class_name="A",
        method_name="testA",
        treatment="A",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response="raw",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cost_usd=0.001,
        latency_ms=100,
    ))
    store.save_llm_call(LLMCall(
        call_type="generation",
        app="mantisbt",
        class_name="B",
        method_name="testB",
        treatment="D",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response="raw",
        input_tokens=300,
        output_tokens=40,
        total_tokens=340,
        cost_usd=0.003,
        latency_ms=300,
    ))

    rows = store.get_llm_usage_summary("treatment")

    assert rows[0]["group_key"] == "A"
    assert rows[0]["calls"] == 1
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["cost_usd"] == 0.001
    assert rows[0]["avg_latency_ms"] == 100
    assert rows[1]["group_key"] == "D"
    assert rows[1]["total_tokens"] == 340
    store.close()


def test_get_llm_usage_summary_total(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_llm_call(LLMCall(
        call_type="generation",
        app="mantisbt",
        class_name="A",
        method_name="testA",
        treatment="A",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response="raw",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cost_usd=0.001,
        latency_ms=100,
    ))

    rows = store.get_llm_usage_summary()

    assert rows == [{
        "group_key": "total",
        "calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.001,
        "avg_latency_ms": 100.0,
    }]
    store.close()
