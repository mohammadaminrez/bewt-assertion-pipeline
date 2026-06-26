from __future__ import annotations

"""Optional observability mirrors for locally stored LLM calls."""

import os
from functools import lru_cache

from .types import LLMCall


def langfuse_enabled() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


@lru_cache(maxsize=1)
def _get_langfuse_client():
    if not langfuse_enabled():
        return None

    host = os.environ.get("LANGFUSE_HOST")
    if host and not os.environ.get("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_BASE_URL"] = host

    # Langfuse uses its own HTTP timeout (LANGFUSE_TIMEOUT, seconds, default 5)
    # for the span exporter — the OTLP env var is ignored. The default 5s is too
    # short for slow links and silently drops the flush, so extend it.
    os.environ.setdefault("LANGFUSE_TIMEOUT", "30")

    try:
        from langfuse import get_client
    except ImportError:
        return None

    return get_client()


def _treatment_label(call: LLMCall) -> str:
    return f"T{call.treatment}" if call.treatment else call.call_type


def _trace_name(call: LLMCall) -> str:
    """Human-scannable trace name: which app, which test, treatment, design."""
    parts = [p for p in (call.app, call.class_name, _treatment_label(call), call.mode) if p]
    return " · ".join(parts) or f"bewt-{call.call_type}"


def _observation_name(call: LLMCall) -> str:
    """The model call itself, identified by call type and model."""
    return f"{call.call_type} · {call.model}" if call.model else call.call_type


def _trace_tags(call: LLMCall) -> list[str]:
    tags = []
    if call.app:
        tags.append(f"app:{call.app}")
    if call.treatment:
        tags.append(f"treatment:{call.treatment}")
    if call.mode:
        tags.append(f"mode:{call.mode}")
    if call.model:
        tags.append(f"model:{call.model}")
    if call.call_type:
        tags.append(f"call_type:{call.call_type}")
    return tags


def emit_llm_call(call: LLMCall) -> None:
    """Mirror one locally persisted LLM call to Langfuse when configured."""
    langfuse = _get_langfuse_client()
    if langfuse is None:
        return

    metadata = {
        "local_call_id": call.id,
        "experiment_id": call.experiment_id,
        "call_type": call.call_type,
        "app": call.app,
        "class_name": call.class_name,
        "method_name": call.method_name,
        "treatment": call.treatment,
        "mode": call.mode,
        "model": call.model,
        "provider": call.provider,
        "prompt_hash": call.prompt_hash,
    }

    try:
        with langfuse.start_as_current_observation(
            as_type="generation",
            name=_observation_name(call),
            model=call.model,
            input=[
                {"role": "system", "content": call.system_prompt},
                {"role": "user", "content": call.user_prompt},
            ],
        ) as generation:
            generation.update(
                output=call.raw_response,
                usage_details=_usage_details(call),
                cost_details=_cost_details(call),
                metadata=metadata,
            )
            # Name the enclosing trace so the trace list shows app/test/mode
            # instead of inheriting the generic generation name.
            generation.update_trace(
                name=_trace_name(call),
                tags=_trace_tags(call),
                metadata=metadata,
            )
    except Exception:
        return


def flush_observability() -> None:
    """Flush pending Langfuse events in short-lived CLI runs."""
    langfuse = _get_langfuse_client()
    if langfuse is None:
        return
    try:
        langfuse.flush()
    except Exception:
        return


def _usage_details(call: LLMCall) -> dict:
    usage = {}
    if call.input_tokens is not None:
        usage["input"] = call.input_tokens
    if call.output_tokens is not None:
        usage["output"] = call.output_tokens
    if call.total_tokens is not None:
        usage["total"] = call.total_tokens
    if call.cached_input_tokens is not None:
        usage["cached_input_tokens"] = call.cached_input_tokens
    if call.cache_creation_input_tokens is not None:
        usage["cache_creation_input_tokens"] = call.cache_creation_input_tokens
    if call.cache_read_input_tokens is not None:
        usage["cache_read_input_tokens"] = call.cache_read_input_tokens
    if call.reasoning_tokens is not None:
        usage["reasoning_tokens"] = call.reasoning_tokens
    return usage


def _cost_details(call: LLMCall) -> dict:
    if call.cost_usd is None:
        return {}
    return {"total": call.cost_usd}
