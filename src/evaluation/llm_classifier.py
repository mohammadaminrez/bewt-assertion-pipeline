from __future__ import annotations

"""Use an LLM to pre-classify generated assertions for manual review."""

import json
from typing import Callable

from ..models import ExperimentResult
from ..llm.client import LLMClient

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
            parsed = _parse_classification(response)
            classifications[key] = parsed
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name} [{r.treatment}]: {parsed}")
        except Exception as e:
            classifications[key] = ""
            if on_progress:
                on_progress(i + 1, total, f"{r.test_record.class_name}: error ({e})")

    return classifications


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
