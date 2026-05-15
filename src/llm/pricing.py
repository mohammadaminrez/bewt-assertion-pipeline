from __future__ import annotations

"""Per-model token pricing and cost calculation.

Prices are USD per 1M tokens and should be reviewed before publishing research
results because provider pricing changes over time.
"""

from dataclasses import dataclass

from .types import LLMResponse


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float | None = None
    cache_creation_input_per_1m: float | None = None


MODEL_PRICING: dict[str, ModelPricing] = {
    # OpenAI text token pricing, checked 2026-05-15.
    "gpt-4o": ModelPricing(input_per_1m=2.50, cached_input_per_1m=1.25, output_per_1m=10.00),
    "gpt-4o-mini": ModelPricing(input_per_1m=0.15, cached_input_per_1m=0.075, output_per_1m=0.60),
    # Anthropic Claude API first-party global pricing, checked 2026-05-15.
    "claude-sonnet-4-20250514": ModelPricing(
        input_per_1m=3.00,
        cache_creation_input_per_1m=3.75,
        cached_input_per_1m=0.30,
        output_per_1m=15.00,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        input_per_1m=1.00,
        cache_creation_input_per_1m=1.25,
        cached_input_per_1m=0.10,
        output_per_1m=5.00,
    ),
    # Gemini Developer API paid-tier standard text pricing, checked 2026-05-15.
    "gemini-2.5-pro": ModelPricing(input_per_1m=1.25, cached_input_per_1m=0.125, output_per_1m=10.00),
    "gemini-2.5-flash": ModelPricing(input_per_1m=0.30, cached_input_per_1m=0.03, output_per_1m=2.50),
    "gemini-2.5-flash-lite": ModelPricing(input_per_1m=0.10, cached_input_per_1m=0.01, output_per_1m=0.40),
}


def calculate_cost_usd(response: LLMResponse) -> float | None:
    """Calculate request cost from provider-reported token counts.

    Cached input tokens are priced separately when both the provider reports them
    and a cached-input rate is known. Unknown models or missing token counts
    return None instead of guessing.
    """
    pricing = MODEL_PRICING.get(response.model)
    if pricing is None:
        return None

    has_input = response.input_tokens is not None
    has_output = response.output_tokens is not None
    if not has_input and not has_output:
        return None

    input_tokens = response.input_tokens or 0
    output_tokens = response.output_tokens or 0
    cache_creation_tokens = response.cache_creation_input_tokens or 0
    cache_read_tokens = response.cache_read_input_tokens
    cached_tokens = response.cached_input_tokens or 0
    if cache_read_tokens is None:
        cache_read_tokens = cached_tokens

    if cache_creation_tokens or response.cache_read_input_tokens is not None:
        input_cost = (input_tokens * pricing.input_per_1m) / 1_000_000
        if pricing.cache_creation_input_per_1m is not None:
            input_cost += (cache_creation_tokens * pricing.cache_creation_input_per_1m) / 1_000_000
        elif cache_creation_tokens:
            input_cost += (cache_creation_tokens * pricing.input_per_1m) / 1_000_000
        if pricing.cached_input_per_1m is not None:
            input_cost += (cache_read_tokens * pricing.cached_input_per_1m) / 1_000_000
        else:
            input_cost += (cache_read_tokens * pricing.input_per_1m) / 1_000_000
    elif pricing.cached_input_per_1m is not None and cached_tokens > 0:
        uncached_input_tokens = max(input_tokens - cached_tokens, 0)
        input_cost = (uncached_input_tokens * pricing.input_per_1m) / 1_000_000
        input_cost += (cached_tokens * pricing.cached_input_per_1m) / 1_000_000
    else:
        input_cost = (input_tokens * pricing.input_per_1m) / 1_000_000

    output_cost = (output_tokens * pricing.output_per_1m) / 1_000_000
    return round(input_cost + output_cost, 12)


def with_cost(response: LLMResponse) -> LLMResponse:
    """Return a copy of the response with cost_usd populated when possible."""
    data = response.to_dict()
    data["cost_usd"] = calculate_cost_usd(response)
    return LLMResponse.from_dict(data)
