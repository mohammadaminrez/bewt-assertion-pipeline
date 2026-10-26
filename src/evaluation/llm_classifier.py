from __future__ import annotations

"""Use an LLM to pre-classify generated assertions for manual review."""

import json
from pathlib import Path
from typing import Callable

from ..models import ExperimentResult
from ..llm.client import LLMClient
from ..llm.types import LLMCall, LLMResponse, hash_prompt
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


CRAWLED_CLASSIFIER_SYSTEM = """You are an expert in Selenium test evaluation. A web crawler (Crawljax) automatically generated a Selenium test that replays a sequence of user actions; an LLM then generated the test's assertion. Crawljax's own built-in oracle for the same point is a generic state-equivalence check: it compares the entire current DOM (and screenshot) against the state observed during crawling, plus generic invariants.

You will be given the action trace, an excerpt of the DOM at the assertion point, and the LLM-generated assertion. Classify the LLM assertion into exactly one category:

- semantic: asserts a meaningful, user-visible outcome of the performed actions (e.g. created content appears, a success message is shown, a value the user entered is displayed) — more expressive than whole-DOM equality
- structural: executable and consistent with the page, but asserts an incidental or generic detail (e.g. a tag exists, the URL merely contains "/admin", boilerplate text present) — no more meaningful than a DOM diff
- wrong_assertion: asserts something inconsistent with the reached state or unrelated to the performed actions
- not_executable: would not compile or references elements/variables that cannot exist

Respond with ONLY a JSON object: {"classification": "<category>", "reason": "<one sentence>"}"""

CRAWLED_CLASSIFIER_USER = """Action trace of the test:
{trace}

DOM at the assertion point (excerpt):
```html
{dom}
```

LLM-generated assertion:
```java
{generated}
```

The assertion {pass_status} when executed against the live application.

Classify the LLM-generated assertion:"""

CRAWLED_VALID_CATEGORIES = {"semantic", "structural", "wrong_assertion", "not_executable"}


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


def pre_classify_crawled_results(
    results: list[ExperimentResult],
    llm: LLMClient,
    state_map: dict[str, dict],
    on_progress: ProgressCallback | None = None,
    store: ResultStore | None = None,
    classifier_model: str | None = None,
    dom_excerpt_chars: int = 6000,
) -> dict[str, str]:
    """Pre-classify crawled-test assertions on the meaningfulness rubric.

    Args:
        results: crawled-source experiment results.
        state_map: ``state_map.json`` content from ingestion — per class name,
            the action trace comment and the final-state DOM path.

    Returns a dict of "app|class_name|treatment|model" -> classification
    (semantic | structural | wrong_assertion | not_executable).
    """
    classifications: dict[str, str] = {}
    total = len(results)

    for i, r in enumerate(results):
        key = f"{r.test_record.app}|{r.test_record.class_name}|{r.treatment}|{r.model}"

        if not r.generated_assertion.strip():
            classifications[key] = "not_executable"
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name}: not_executable (empty)")
            continue

        entry = state_map.get(r.test_record.class_name, {})
        trace = entry.get("trace_comment", "// (no trace available)")
        dom = ""
        dom_path = entry.get("dom_path")
        if dom_path and Path(dom_path).exists():
            dom = Path(dom_path).read_text()[:dom_excerpt_chars]

        pass_status = "PASSED" if r.passes else "FAILED"
        user_msg = CRAWLED_CLASSIFIER_USER.format(
            trace=trace, dom=dom or "(no DOM captured)",
            generated=r.generated_assertion, pass_status=pass_status,
        )

        try:
            response = llm.generate(CRAWLED_CLASSIFIER_SYSTEM, user_msg)
            parsed = _parse_classification(response.text, valid=CRAWLED_VALID_CATEGORIES)
            classifications[key] = parsed
            if store:
                _save_and_emit_llm_call(store, _build_crawled_pre_classification_call(
                    r, classifier_model or response.model, user_msg, response
                ))
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name} [{r.treatment}]: {parsed}")
        except Exception as e:
            classifications[key] = ""
            if store:
                error_response = LLMResponse(text=str(e), provider="",
                                             model=classifier_model or r.model)
                _save_and_emit_llm_call(store, _build_crawled_pre_classification_call(
                    r, classifier_model or r.model, user_msg, error_response
                ))
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name}: error ({e})")

    flush_observability()
    return classifications


def _build_crawled_pre_classification_call(
    result: ExperimentResult,
    classifier_model: str,
    user_msg: str,
    response: LLMResponse,
) -> LLMCall:
    call = _build_pre_classification_call(result, classifier_model, user_msg, response)
    call.system_prompt = CRAWLED_CLASSIFIER_SYSTEM
    call.prompt_hash = hash_prompt(call.system_prompt, call.user_prompt)
    call.source = "crawled"
    return call


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
        from_cache=response.from_cache,
        served_model=response.served_model,
    )


def _parse_classification(response: str, valid: set[str] | None = None) -> str:
    if valid is None:
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
