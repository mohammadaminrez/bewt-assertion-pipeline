from __future__ import annotations

import pytest

from src.models import TestRecord
from src.llm.prompt_builder import (
    TREATMENTS,
    Ingredients,
    build_assertion_prompt,
    treatment_ingredients,
)


def _record() -> TestRecord:
    return TestRecord(
        app="mantisbt", variant="base", version="2.25.7",
        file_path="X.java", class_name="AddCategoryTest", method_name="addCategory",
        stripped_source="// TODO: Insert the missing assertion here",
    )


def test_treatments_are_strictly_additive():
    assert treatment_ingredients("A") == Ingredients(False, False, False)
    assert treatment_ingredients("B") == Ingredients(True, False, False)
    assert treatment_ingredients("C") == Ingredients(True, True, False)
    assert treatment_ingredients("D") == Ingredients(True, True, True)


def test_unknown_treatment_is_rejected():
    assert TREATMENTS == ("A", "B", "C", "D")
    with pytest.raises(ValueError):
        treatment_ingredients("E")


def test_prompt_only_describes_included_sources():
    system, user = build_assertion_prompt(
        _record(),
        variant_source="// TODO",
        include_comments=False,
        html_content="<html><body>Category001</body></html>",
        strings_source=None,
        page_object_sources=None,
    )
    assert "Descriptive comments have been added" not in user
    assert "HTML page content at the assertion point" in user
    assert "Project constants" not in user
    assert "Page Object classes" not in user


def test_prompt_includes_all_sources_for_full_condition():
    system, user = build_assertion_prompt(
        _record(),
        variant_source="// Assert that ...\n// TODO",
        include_comments=True,
        html_content="<html></html>",
        strings_source="public static final String FOO = \"bar\";",
        page_object_sources={"EditProjectPage": "class EditProjectPage {}"},
    )
    assert "Descriptive comments have been added" in user
    assert "HTML page content at the assertion point" in user
    assert "Project constants (Strings.java)" in user
    assert "EditProjectPage.java" in user


def test_prompt_contains_the_whole_page_unchanged():
    body = "<p>row</p>" * 5000 + '<td id="last">Category001</td>'
    page = ("<html><head><title>Categories - Bludit</title><style>.x{color:red}</style></head>"
            f'<body><div id="alert" style="display: none;"></div>{body}<!-- note --></body></html>')
    _, user = build_assertion_prompt(
        _record(), variant_source="// TODO", include_comments=False, html_content=page,
    )
    assert f"```html\n{page}\n```" in user  # character for character, nothing cut or cleaned
