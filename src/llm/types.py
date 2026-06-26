from __future__ import annotations

"""Shared LLM client response types."""

import hashlib
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
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
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
            cache_creation_input_tokens=data.get("cache_creation_input_tokens"),
            cache_read_input_tokens=data.get("cache_read_input_tokens"),
            reasoning_tokens=data.get("reasoning_tokens"),
            cost_usd=data.get("cost_usd"),
            latency_ms=data.get("latency_ms"),
        )


@dataclass
class LLMCall:
    """Auditable record of one prompt/response exchange with an LLM provider."""

    call_type: str
    app: str
    class_name: str
    method_name: str
    treatment: str
    model: str
    provider: str
    system_prompt: str
    user_prompt: str
    raw_response: str
    experiment_id: int | None = None
    prompt_hash: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    id: int | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.prompt_hash is None:
            self.prompt_hash = hash_prompt(self.system_prompt, self.user_prompt)

    def to_dict(self) -> dict:
        return asdict(self)


def hash_prompt(system_prompt: str, user_prompt: str) -> str:
    """Stable hash for deduplicating exact system/user prompt pairs."""
    return hashlib.sha256(f"{system_prompt}|||{user_prompt}".encode()).hexdigest()
