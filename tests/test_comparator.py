"""Tests for the assertion comparator and evaluation metrics."""

from src.models import AssertionRecord, AssertionType, ErrorCategory
from src.evaluation.comparator import (
    check_exact_match,
    compute_semantic_similarity,
    classify_error,
    _normalize,
    _extract_string_literals,
)


def _rec(text, atype=AssertionType.ASSERT_EQUALS, expected=None, actual=None):
    return AssertionRecord(
        assertion_type=atype, full_text=text,
        start_line=1, end_line=1,
        expected_value=expected, actual_expression=actual,
    )


# --- exact match ---

def test_exact_match_identical():
    gold = [_rec('assertEquals("hello", foo.bar());')]
    assert check_exact_match('assertEquals("hello", foo.bar());', gold)


def test_exact_match_whitespace_difference():
    # _normalize collapses runs of whitespace and strips "( ", " )", " ;"
    gold = [_rec('assertEquals("hello",  foo.bar()) ;')]
    assert check_exact_match('assertEquals("hello", foo.bar());', gold)


def test_exact_match_different():
    gold = [_rec('assertEquals("hello", foo.bar());')]
    assert not check_exact_match('assertEquals("world", foo.bar());', gold)


# --- semantic similarity ---

def test_similarity_identical():
    gold = [_rec('assertEquals("hello", foo.bar());')]
    sim = compute_semantic_similarity('assertEquals("hello", foo.bar());', gold)
    assert sim == 1.0


def test_similarity_completely_different():
    gold = [_rec('assertEquals("hello", foo.bar());')]
    sim = compute_semantic_similarity('assertTrue(baz.isReady());', gold)
    assert sim < 0.3


def test_similarity_same_type_different_value():
    gold = [_rec('assertEquals("hello", foo.bar());')]
    sim = compute_semantic_similarity('assertEquals("world", foo.bar());', gold)
    assert 0.3 < sim < 0.9  # type matches, value doesn't, text partially matches


def test_similarity_swapped_assertEquals_args():
    gold = [_rec('assertEquals(foo.bar(), "hello");')]
    sim = compute_semantic_similarity('assertEquals("hello", foo.bar());', gold)
    # Same string literals, same type, text differs by arg order
    assert sim > 0.6


def test_similarity_assertTrue_no_string_literals():
    gold = [_rec('assertTrue(page.isLoaded());', atype=AssertionType.ASSERT_TRUE)]
    sim = compute_semantic_similarity('assertTrue(page.isLoaded());', gold)
    assert sim == 1.0


def test_similarity_empty_generated():
    gold = [_rec('assertEquals("hello", foo.bar());')]
    sim = compute_semantic_similarity('', gold)
    assert sim == 0.0


def test_similarity_multiple_assertions():
    gold = [
        _rec('assertTrue(a.hasError());', atype=AssertionType.ASSERT_TRUE),
        _rec('assertTrue(b.hasError());', atype=AssertionType.ASSERT_TRUE),
    ]
    gen = 'assertTrue(a.hasError());\nassertTrue(b.hasError());'
    sim = compute_semantic_similarity(gen, gold)
    assert sim == 1.0


# --- classify error ---

def test_classify_error_not_executable():
    gold = [_rec('assertEquals("x", foo());')]
    cat = classify_error('assertEquals("x", foo());', gold, compiles=False, passes=False)
    assert cat == ErrorCategory.NOT_EXECUTABLE


def test_classify_error_correct_exact():
    gold = [_rec('assertEquals("x", foo());')]
    cat = classify_error('assertEquals("x", foo());', gold, compiles=True, passes=True)
    assert cat == ErrorCategory.CORRECT


def test_classify_error_over_assertive():
    gold = [_rec('assertTrue(a());', atype=AssertionType.ASSERT_TRUE)]
    gen = 'assertTrue(a());\nassertTrue(b());'
    cat = classify_error(gen, gold, compiles=True, passes=True)
    assert cat == ErrorCategory.OVER_ASSERTIVE


def test_classify_error_wrong_assertion():
    gold = [_rec('assertEquals("x", foo());')]
    cat = classify_error('assertEquals("y", foo());', gold, compiles=True, passes=False)
    assert cat == ErrorCategory.WRONG_ASSERTION


# --- helpers ---

def test_normalize_strips_whitespace():
    # _normalize: collapses whitespace, strips "( " → "(", " )" → ")", " ;" → ";"
    assert _normalize('assertEquals( "x" , foo() ) ;') == 'assertEquals("x" , foo());'


def test_extract_string_literals():
    code = 'assertEquals("hello world", foo.getText());'
    assert _extract_string_literals(code) == {"hello world"}


def test_extract_string_literals_multiple():
    code = 'assertEquals("a", x());\nassertEquals("b", y());'
    assert _extract_string_literals(code) == {"a", "b"}
