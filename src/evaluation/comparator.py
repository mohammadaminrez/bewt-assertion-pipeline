from __future__ import annotations

"""Compare generated assertions with gold-standard assertions."""

import hashlib
import re
from ..models import AssertionRecord, AssertionType, ErrorCategory


_MARKER_LINE = re.compile(r"^\s*//\s*\(\d+\)\s*$", re.MULTILINE)


def _strip_markers(code: str) -> str:
    """Drop the `// (n)` placeholder markers; gold assertions carry none."""
    return _MARKER_LINE.sub("", code)


# Exceptions that mean "an assertion was evaluated and did not hold". Any
# other exception is a runtime error in the generated code (e.g. a locator
# that finds nothing) and makes the answer not executable.
ASSERTION_FAILURES = {
    "java.lang.AssertionError",
    "org.junit.ComparisonFailure",
    "junit.framework.AssertionFailedError",
    "junit.framework.ComparisonFailure",
}


def classify_outcome(
    generated: str,
    gold_assertions: list[AssertionRecord],
    compiles: bool,
    passes: bool,
    error_type: str = "",
) -> ErrorCategory:
    """Category from execution facts alone; only certain cases are decided.

    - does not compile, or throws a non-assertion exception -> not_executable
    - runs and an assertion fails                            -> wrong_assertion
    - passes and equals the gold (whitespace-insensitive)    -> correct
    - passes but differs                                     -> needs_review
    """
    if not compiles or not generated.strip():
        return ErrorCategory.NOT_EXECUTABLE
    if not passes:
        if error_type in ASSERTION_FAILURES:
            return ErrorCategory.WRONG_ASSERTION
        return ErrorCategory.NOT_EXECUTABLE
    if check_exact_match(generated, gold_assertions):
        return ErrorCategory.CORRECT
    return ErrorCategory.NEEDS_REVIEW


def compute_semantic_similarity(
    generated: str,
    gold_assertions: list[AssertionRecord],
) -> float:
    """Compute semantic similarity between generated and gold assertions (0-1)."""
    generated = _strip_markers(generated)
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


def answer_key(code: str) -> str:
    """Identity of an answer for review: equal keys = same code up to
    whitespace and placeholder markers, so one label covers every run."""
    return hashlib.sha1(_normalize(_strip_markers(code)).encode()).hexdigest()[:12]


def check_exact_match(generated: str, gold_assertions: list[AssertionRecord]) -> bool:
    """Check if the generated assertion exactly matches the gold standard."""
    generated = _strip_markers(generated)
    gold_text = "\n".join(a.full_text for a in gold_assertions)
    return _normalize(generated) == _normalize(gold_text)


def _normalize(text: str) -> str:
    """Normalize Java code for comparison: strip whitespace, normalize strings."""
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('( ', '(').replace(' )', ')')
    text = text.replace(' ;', ';')
    return text


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
