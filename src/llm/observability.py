from __future__ import annotations

"""Optional PostHog LLM-analytics mirror for locally stored LLM calls."""

import os
import uuid
import warnings
from functools import lru_cache

from .types import LLMCall

_REQUIRED_ENV = ("POSTHOG_PROJECT_TOKEN", "POSTHOG_HOST")

# The pipeline has no end users: every event is attributed to the pipeline
# itself and sent personless, so no person profiles are created.
DISTINCT_ID = "bewt-pipeline"

# One CLI invocation is one PostHog AI session, so a whole experiment run
# can be inspected together in the Sessions tab.
RUN_ID = str(uuid.uuid4())


def posthog_enabled() -> bool:
    return all(os.environ.get(var) for var in _REQUIRED_ENV)


@lru_cache(maxsize=1)
def _get_posthog_client():
    for var in _REQUIRED_ENV:
        if not os.environ.get(var):
            # A missing config must not break a run, but must not be silent
            # either: warn once per process.
            warnings.warn(
                f"{var} variable required by PostHog is missing or un-configured, "
                "this causes events to be silently missed. "
                f"This error stops appearing once {var} is configured"
            )
            return None

    try:
        from posthog import Posthog
    except ImportError:
        return None

    return Posthog(os.environ["POSTHOG_PROJECT_TOKEN"], host=os.environ["POSTHOG_HOST"])


def _treatment_label(call: LLMCall) -> str:
    return f"T{call.treatment}" if call.treatment else call.call_type


def _span_name(call: LLMCall) -> str:
    """Human-scannable name: which app, which test, treatment, source."""
    parts = [p for p in (call.app, call.class_name, _treatment_label(call), call.source) if p]
    return " · ".join(parts) or f"bewt-{call.call_type}"


def _trace_id(call: LLMCall) -> str:
    """Group every call for one test and model within a run into one trace,
    so the A/B/C/D generations of a test sit side by side."""
    return ":".join([RUN_ID, call.source, call.app, call.class_name, call.model])


def _generation_properties(call: LLMCall) -> dict:
    props = {
        "$ai_trace_id": _trace_id(call),
        "$ai_session_id": RUN_ID,
        "$ai_span_name": _span_name(call),
        "$ai_model": call.model,
        "$ai_provider": call.provider,
        "$ai_input": [
            {"role": "system", "content": call.system_prompt},
            {"role": "user", "content": call.user_prompt},
        ],
        "$ai_output_choices": [{"role": "assistant", "content": call.raw_response}],
        "$process_person_profile": False,
        # Research metadata for filtering and breakdowns.
        "local_call_id": call.id,
        "experiment_id": call.experiment_id,
        "call_type": call.call_type,
        "app": call.app,
        "class_name": call.class_name,
        "method_name": call.method_name,
        "treatment": call.treatment,
        "source": call.source,
        "prompt_hash": call.prompt_hash,
        "served_model": call.served_model,
    }
    if call.input_tokens is not None:
        props["$ai_input_tokens"] = call.input_tokens
    if call.output_tokens is not None:
        props["$ai_output_tokens"] = call.output_tokens
    # OpenAI/Gemini report only `cached_input_tokens` (already included in
    # input tokens); Anthropic reports read/creation separately (excluded).
    # PostHog's per-provider default for cache exclusivity matches both.
    cache_read = (
        call.cache_read_input_tokens
        if call.cache_read_input_tokens is not None
        else call.cached_input_tokens
    )
    if cache_read is not None:
        props["$ai_cache_read_input_tokens"] = cache_read
    if call.cache_creation_input_tokens is not None:
        props["$ai_cache_creation_input_tokens"] = call.cache_creation_input_tokens
    if call.reasoning_tokens is not None:
        props["reasoning_tokens"] = call.reasoning_tokens
    if call.latency_ms is not None:
        props["$ai_latency"] = call.latency_ms / 1000
    if call.cost_usd is not None:
        # Keep PostHog's cost identical to the locally stored one.
        props["$ai_total_cost_usd"] = call.cost_usd
        props["$ai_cost_passthrough"] = True
    if not call.provider:
        # The runner records failed provider calls with no provider and the
        # exception text as the response.
        props["$ai_is_error"] = True
        props["$ai_error"] = call.raw_response
    return props


def emit_llm_call(call: LLMCall) -> None:
    """Mirror one locally persisted LLM call to PostHog when configured.

    Cache hits are not sent: PostHog shows only real API traffic and spend,
    while the local database keeps every call, flagged with `from_cache`.
    """
    if call.from_cache:
        return
    posthog = _get_posthog_client()
    if posthog is None:
        return
    try:
        posthog.capture(
            "$ai_generation",
            distinct_id=DISTINCT_ID,
            properties=_generation_properties(call),
        )
    except Exception:
        return


def flush_observability() -> None:
    """Flush pending PostHog events in short-lived CLI runs."""
    posthog = _get_posthog_client()
    if posthog is None:
        return
    try:
        posthog.flush()
    except Exception:
        return
