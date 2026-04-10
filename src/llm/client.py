from __future__ import annotations

"""Unified LLM client supporting multiple providers."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Protocol

from ..config import Config


class LLMClient(Protocol):
    def generate(self, system: str, user: str) -> str: ...


class OpenAIClient:
    def __init__(self, model_id: str, temperature: float, max_tokens: int, api_key: str):
        import openai
        self.client = openai.OpenAI(api_key=api_key)
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content


class AnthropicClient:
    def __init__(self, model_id: str, temperature: float, max_tokens: int, api_key: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model=self.model_id,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.content[0].text


class CachedClient:
    """Wraps an LLM client with disk-based response caching."""

    def __init__(self, client: LLMClient, cache_dir: Path):
        self.client = client
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, system: str, user: str) -> str:
        key = hashlib.sha256(f"{system}|||{user}".encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"

        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            return data["response"]

        response = self.client.generate(system, user)
        cache_file.write_text(json.dumps({
            "system": system,
            "user": user,
            "response": response,
        }, indent=2))
        return response


class RetryClient:
    """Wraps an LLM client with retry logic."""

    def __init__(self, client: LLMClient, max_retries: int = 3, delay: float = 5.0):
        self.client = client
        self.max_retries = max_retries
        self.delay = delay

    def generate(self, system: str, user: str) -> str:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return self.client.generate(system, user)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.delay * (attempt + 1))
        raise last_error


def create_client(config: Config, model_name: str | None = None) -> LLMClient:
    """Create an LLM client from config."""
    model_name = model_name or config.default_model
    model_config = config.models[model_name]
    provider = model_config["provider"]
    api_key = os.environ.get(model_config["api_key_env"], "")

    if not api_key:
        raise ValueError(
            f"API key not set. Please set the {model_config['api_key_env']} environment variable."
        )

    if provider == "openai":
        client = OpenAIClient(
            model_id=model_config["model_id"],
            temperature=model_config["temperature"],
            max_tokens=model_config["max_tokens"],
            api_key=api_key,
        )
    elif provider == "anthropic":
        client = AnthropicClient(
            model_id=model_config["model_id"],
            temperature=model_config["temperature"],
            max_tokens=model_config["max_tokens"],
            api_key=api_key,
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")

    # Wrap with retry
    client = RetryClient(client, max_retries=config.retry_attempts)

    # Wrap with cache if enabled
    if config.cache_responses:
        cache_dir = config.output_dir / "cache" / model_name
        client = CachedClient(client, cache_dir)

    return client
