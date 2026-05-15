from __future__ import annotations

"""Use an LLM to pre-classify generated assertions for manual review."""

import json
from typing import Callable

from ..models import ExperimentResult
from ..llm.client import LLMClient
from ..llm.types import LLMCall, LLMResponse
from ..llm.observability import emit_llm_call, flush_observability
from ..data.store import ResultStore

CLASSIFIER_SYSTEM = """You are an expert in Selenium test evaluation. You will be given a gold-standard assertion and a generated assertion from a Selenium test.

Classify the generated assertion into exactly one category:

- correct: Semantically equivalent to the gold standard (may have different syntax)
- over_assertive: Checks MORE than the gold standard (extra assertions or stricter)
- under_assertive: Checks LESS than the gold standard (weaker, would pass when it should fail)
- wrong_assertion: Checks the WRONG thing (different element, value, or logic)
- not_executable: Does not compile or cannot run (syntax error, missing method)

Respond with ONLY a JSON object: {"classification": "<category>", "reason": "<one sentence>"}"""

CLASSIFIER_USER = """Gold standard assertion:
```java
{gold}
```

Generated assertion:
```java
{generated}
```

The generated assertion {pass_status} when executed.

Classify the generated assertion:"""


ProgressCallback = Callable[[int, int, str], None]


def pre_classify_results(
    results: list[ExperimentResult],
    llm: LLMClient,
    on_progress: ProgressCallback | None = None,
    store: ResultStore | None = None,
    classifier_model: str | None = None,
) -> dict[str, str]:
    """Pre-classify all results using an LLM.

    Returns a dict of "app|class_name|treatment|model" -> classification.
    """
    classifications = {}
    total = len(results)

    for i, r in enumerate(results):
        key = f"{r.test_record.app}|{r.test_record.class_name}|{r.treatment}|{r.model}"

        if not r.generated_assertion.strip():
            classifications[key] = "not_executable"
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name}: not_executable (empty)")
            continue

        pass_status = "PASSED" if r.passes else "FAILED"
        user_msg = CLASSIFIER_USER.format(
            gold=r.test_record.gold_standard,
            generated=r.generated_assertion,
            pass_status=pass_status,
        )

        try:
            response = llm.generate(CLASSIFIER_SYSTEM, user_msg)
            parsed = _parse_classification(response.text)
            classifications[key] = parsed
            if store:
                _save_and_emit_llm_call(store, _build_pre_classification_call(
                    r, classifier_model or response.model, user_msg, response
                ))
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name} [{r.treatment}]: {parsed}")
        except Exception as e:
            classifications[key] = ""
            if store:
                error_response = LLMResponse(
                    text=str(e),
                    provider="",
                    model=classifier_model or r.model,
                )
                _save_and_emit_llm_call(store, _build_pre_classification_call(
                    r, classifier_model or r.model, user_msg, error_response
                ))
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name}: error ({e})")

    flush_observability()
    return classifications


def _save_and_emit_llm_call(store: ResultStore, call: LLMCall) -> int:
    call_id = store.save_llm_call(call)
    call.id = call_id
    emit_llm_call(call)
    return call_id


def _build_pre_classification_call(
    result: ExperimentResult,
    classifier_model: str,
    user_msg: str,
    response: LLMResponse,
) -> LLMCall:
    return LLMCall(
        call_type="pre_classification",
        app=result.test_record.app,
        class_name=result.test_record.class_name,
        method_name=result.test_record.method_name,
        treatment=result.treatment,
        model=classifier_model,
        provider=response.provider,
        system_prompt=CLASSIFIER_SYSTEM,
        user_prompt=user_msg,
        raw_response=response.text,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        cached_input_tokens=response.cached_input_tokens,
        cache_creation_input_tokens=response.cache_creation_input_tokens,
        cache_read_input_tokens=response.cache_read_input_tokens,
        reasoning_tokens=response.reasoning_tokens,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
    )


def _parse_classification(response: str) -> str:
    valid = {"correct", "over_assertive", "under_assertive", "wrong_assertion", "not_executable"}

    try:
        start = response.index("{")
        end = response.rindex("}") + 1
        data = json.loads(response[start:end])
        classification = data.get("classification", "").strip()
        if classification in valid:
            return classification
    except (ValueError, json.JSONDecodeError):
        pass

    response_lower = response.lower()
    for cat in valid:
        if cat in response_lower:
            return cat

    return ""
