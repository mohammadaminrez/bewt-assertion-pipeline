from __future__ import annotations

from src.llm.pricing import calculate_cost_usd
from src.llm.types import LLMResponse


def test_calculate_cost_uses_input_output_and_cached_tokens():
    response = LLMResponse(
        text="ok",
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=1_000_000,
        cached_input_tokens=250_000,
        output_tokens=500_000,
        total_tokens=1_500_000,
    )

    assert calculate_cost_usd(response) == 0.43125


def test_calculate_cost_returns_none_for_unknown_model():
    response = LLMResponse(
        text="ok",
        provider="unknown",
        model="unpriced-model",
        input_tokens=100,
        output_tokens=100,
    )

    assert calculate_cost_usd(response) is None


def test_calculate_cost_returns_none_when_usage_missing():
    response = LLMResponse(text="ok", provider="openai", model="gpt-4o")

    assert calculate_cost_usd(response) is None


def test_calculate_cost_prices_cache_writes_and_reads_separately():
    response = LLMResponse(
        text="ok",
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        input_tokens=1_000_000,
        cache_creation_input_tokens=100_000,
        cache_read_input_tokens=200_000,
        output_tokens=500_000,
    )

    assert calculate_cost_usd(response) == 10.935
