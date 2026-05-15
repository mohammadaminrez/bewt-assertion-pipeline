from __future__ import annotations

"""Shared LLM client response types."""

from dataclasses import asdict, dataclass


@dataclass
class LLMResponse:
    """Text plus provider usage metadata for a single LLM call."""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LLMResponse":
        return cls(
            text=data.get("text", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            total_tokens=data.get("total_tokens"),
            cached_input_tokens=data.get("cached_input_tokens"),
            reasoning_tokens=data.get("reasoning_tokens"),
            cost_usd=data.get("cost_usd"),
            latency_ms=data.get("latency_ms"),
        )
