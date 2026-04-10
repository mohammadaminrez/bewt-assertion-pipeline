from __future__ import annotations

"""Compare generated assertions with gold-standard assertions."""

import re
from ..parsing.assertion_model import AssertionRecord, AssertionType, ErrorCategory


def classify_error(
    generated: str,
    gold_assertions: list[AssertionRecord],
    compiles: bool,
    passes: bool,
) -> ErrorCategory:
    """Classify the error type of a generated assertion."""
    if not compiles:
        return ErrorCategory.NOT_EXECUTABLE

    if not generated.strip():
        return ErrorCategory.NOT_EXECUTABLE

    # Check exact match
    gold_text = "\n".join(a.full_text for a in gold_assertions)
    if _normalize(generated) == _normalize(gold_text):
        return ErrorCategory.CORRECT

    if passes:
        # Passes but different from gold — could be correct, over, or under assertive
        gen_count = _count_assertions(generated)
        gold_count = len(gold_assertions)

        if gen_count > gold_count:
            return ErrorCategory.OVER_ASSERTIVE
        elif gen_count < gold_count:
            return ErrorCategory.UNDER_ASSERTIVE

        # Same count but different — check if semantically equivalent
        if _is_semantically_equivalent(generated, gold_assertions):
            return ErrorCategory.CORRECT
        else:
            # Passes but tests different things
            return ErrorCategory.WRONG_ASSERTION
    else:
        # Fails — wrong assertion
        return ErrorCategory.WRONG_ASSERTION


def compute_semantic_similarity(
    generated: str,
    gold_assertions: list[AssertionRecord],
) -> float:
    """Compute semantic similarity between generated and gold assertions (0-1)."""
    if not generated.strip():
        return 0.0

    scores = []

    # 1. Assertion type match
    gen_types = _extract_assertion_types(generated)
    gold_types = [a.assertion_type for a in gold_assertions]
    type_score = _set_similarity(
        {t.value for t in gen_types},
        {t.value for t in gold_types},
    )
    scores.append(type_score * 0.2)

    # 2. Expected value similarity
    # Extract string literals from both generated and gold full_text to avoid
    # issues with: (a) non-assertEquals having no parsed expected_value, and
    # (b) assertEquals with swapped argument order storing the wrong field.
    gen_expected = _extract_string_literals(generated)
    gold_text_combined = "\n".join(a.full_text for a in gold_assertions)
    gold_expected = _extract_string_literals(gold_text_combined)
    if gold_expected or gen_expected:
        exp_score = _set_similarity(gen_expected, gold_expected)
    else:
        exp_score = 1.0 if not gen_expected and not gold_expected else 0.0
    scores.append(exp_score * 0.4)

    # 3. Textual similarity (normalized Levenshtein)
    gold_text = "\n".join(a.full_text for a in gold_assertions)
    text_score = _normalized_levenshtein(
        _normalize(generated),
        _normalize(gold_text),
    )
    scores.append(text_score * 0.4)

    return sum(scores)


def check_exact_match(generated: str, gold_assertions: list[AssertionRecord]) -> bool:
    """Check if the generated assertion exactly matches the gold standard."""
    gold_text = "\n".join(a.full_text for a in gold_assertions)
    return _normalize(generated) == _normalize(gold_text)


def _normalize(text: str) -> str:
    """Normalize Java code for comparison: strip whitespace, normalize strings."""
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('( ', '(').replace(' )', ')')
    text = text.replace(' ;', ';')
    return text


def _count_assertions(code: str) -> int:
    """Count assertion calls in code."""
    return len(re.findall(r'assert(?:Equals|True|False|NotNull|Null)\s*\(', code))


def _extract_assertion_types(code: str) -> list[AssertionType]:
    """Extract assertion types from code."""
    types = []
    type_map = {
        'assertEquals': AssertionType.ASSERT_EQUALS,
        'assertTrue': AssertionType.ASSERT_TRUE,
        'assertFalse': AssertionType.ASSERT_FALSE,
        'assertNotNull': AssertionType.ASSERT_NOT_NULL,
        'assertNull': AssertionType.ASSERT_NULL,
    }
    for match in re.finditer(r'(assert(?:Equals|True|False|NotNull|Null))\s*\(', code):
        name = match.group(1)
        if name in type_map:
            types.append(type_map[name])
    return types


def _extract_string_literals(code: str) -> set[str]:
    """Extract string literals from code."""
    return set(re.findall(r'"((?:[^"\\]|\\.)*)"', code))


def _set_similarity(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union) if union else 0.0


def _is_semantically_equivalent(generated: str, gold_assertions: list[AssertionRecord]) -> bool:
    """Heuristic check for semantic equivalence."""
    gen_literals = _extract_string_literals(generated)
    gold_literals = set()
    for a in gold_assertions:
        if a.expected_value:
            val = a.resolved_expected or a.expected_value
            gold_literals.add(val.strip('"'))
        gold_literals.update(_extract_string_literals(a.full_text))

    # If all expected values match, consider it equivalent
    return gen_literals == gold_literals


def _normalized_levenshtein(s1: str, s2: str) -> float:
    """Normalized Levenshtein similarity (1 = identical, 0 = completely different)."""
    if s1 == s2:
        return 1.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0

    try:
        import Levenshtein
        distance = Levenshtein.distance(s1, s2)
    except ImportError:
        # Fallback: simple ratio
        distance = _simple_edit_distance(s1, s2)

    return 1.0 - (distance / max_len)


def _simple_edit_distance(s1: str, s2: str) -> int:
    """Simple edit distance (for fallback when Levenshtein not installed)."""
    if len(s1) < len(s2):
        return _simple_edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]
