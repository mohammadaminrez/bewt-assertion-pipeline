from __future__ import annotations

from src.data.store import ResultStore
from src.llm.types import LLMCall, hash_prompt


def test_result_store_creates_llm_calls_table(tmp_path):
    store = ResultStore(tmp_path / "results.db")

    columns = {
        row["name"]
        for row in store.conn.execute("PRAGMA table_info(llm_calls)").fetchall()
    }

    assert {
        "id",
        "experiment_id",
        "call_type",
        "app",
        "class_name",
        "method_name",
        "treatment",
        "model",
        "provider",
        "system_prompt",
        "user_prompt",
        "prompt_hash",
        "raw_response",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "reasoning_tokens",
        "cost_usd",
        "latency_ms",
        "created_at",
    }.issubset(columns)
    store.close()


def test_save_and_filter_llm_call(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    call = LLMCall(
        call_type="generation",
        app="mantisbt",
        class_name="AddNewProject",
        method_name="addNewProject",
        treatment="D",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response='assertEquals("x", page.value());',
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

    call_id = store.save_llm_call(call)
    rows = store.get_llm_calls(treatment="D", model="gpt-4o-mini")

    assert call_id == 1
    assert len(rows) == 1
    assert rows[0]["call_type"] == "generation"
    assert rows[0]["app"] == "mantisbt"
    assert rows[0]["class_name"] == "AddNewProject"
    assert rows[0]["method_name"] == "addNewProject"
    assert rows[0]["provider"] == "openai"
    assert rows[0]["system_prompt"] == "system"
    assert rows[0]["user_prompt"] == "user"
    assert rows[0]["prompt_hash"] == hash_prompt("system", "user")
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 20
    assert rows[0]["total_tokens"] == 120
    assert rows[0]["cached_input_tokens"] == 10
    assert rows[0]["cache_creation_input_tokens"] == 3
    assert rows[0]["cache_read_input_tokens"] == 7
    assert rows[0]["reasoning_tokens"] == 5
    assert rows[0]["cost_usd"] == 0.0012
    assert rows[0]["latency_ms"] == 345

    assert store.get_llm_calls(treatment="A") == []
    store.close()
