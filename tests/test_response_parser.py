"""Tests for the LLM response parser."""

from src.llm.response_parser import extract_assertion_from_response, validate_assertion


# --- extraction ---

def test_extract_plain_assertion():
    response = 'assertEquals("hello", foo.bar());'
    assert extract_assertion_from_response(response) == 'assertEquals("hello", foo.bar());'


def test_extract_from_markdown_block():
    response = '```java\nassertEquals("hello", foo.bar());\n```'
    assert extract_assertion_from_response(response) == 'assertEquals("hello", foo.bar());'


def test_extract_from_markdown_block_no_lang():
    response = '```\nassertTrue(page.isLoaded());\n```'
    assert extract_assertion_from_response(response) == 'assertTrue(page.isLoaded());'


def test_extract_multiple_assertions():
    response = '```java\nassertTrue(a.hasError());\nassertTrue(b.hasError());\n```'
    result = extract_assertion_from_response(response)
    assert 'assertTrue(a.hasError());' in result
    assert 'assertTrue(b.hasError());' in result


def test_extract_with_explanation():
    response = (
        'The assertion should check the alert message:\n\n'
        '```java\nassertEquals("Please supply a review title", product.getAlertMessage());\n```\n\n'
        'This verifies the error message is shown.'
    )
    assert 'assertEquals("Please supply a review title", product.getAlertMessage());' in extract_assertion_from_response(response)


def test_extract_empty_response():
    assert extract_assertion_from_response('') == ''


def test_extract_no_assertion():
    response = 'I cannot determine the assertion without more context.'
    result = extract_assertion_from_response(response)
    # Should return cleaned text (no assertion found)
    assert 'assert' not in result.lower() or result == ''


# --- validation ---

def test_validate_valid_assertEquals():
    assert validate_assertion('assertEquals("hello", foo.bar());')


def test_validate_valid_assertTrue():
    assert validate_assertion('assertTrue(page.isLoaded());')


def test_validate_valid_multiple():
    assert validate_assertion('assertTrue(a.hasError());\nassertTrue(b.hasError());')


def test_validate_empty():
    assert not validate_assertion('')


def test_validate_unbalanced_parens():
    assert not validate_assertion('assertEquals("hello", foo.bar(;')


def test_validate_no_semicolon():
    assert not validate_assertion('assertEquals("hello", foo.bar())')


def test_validate_not_an_assertion():
    assert not validate_assertion('String result = foo.bar();')
