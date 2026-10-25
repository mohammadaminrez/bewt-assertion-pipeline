from __future__ import annotations

import pytest

import src.llm.observability as observability


@pytest.fixture(autouse=True)
def _no_real_posthog(monkeypatch):
    """Never send test traffic to the real PostHog project.

    Importing `src.cli` loads the developer's `.env`, which holds the real
    PostHog token. Tests that need PostHog set fake values themselves.
    """
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    observability._get_posthog_client.cache_clear()
    yield
    observability._get_posthog_client.cache_clear()
