from __future__ import annotations

import pytest

from src.models import TestRecord
from src.llm.prompt_builder import (
    MODE_CUMULATIVE,
    MODE_SINGULAR,
    Ingredients,
    build_assertion_prompt,
    treatment_ingredients,
    valid_treatments,
)


def _record() -> TestRecord:
    return TestRecord(
        app="mantisbt", variant="base", version="2.25.7",
        file_path="X.java", class_name="AddCategoryTest", method_name="addCategory",
        stripped_source="// TODO: Insert the missing assertion here",
    )


def test_cumulative_is_strictly_additive():
    assert treatment_ingredients(MODE_CUMULATIVE, "A") == Ingredients(False, False, False)
    assert treatment_ingredients(MODE_CUMULATIVE, "B") == Ingredients(True, False, False)
    assert treatment_ingredients(MODE_CUMULATIVE, "C") == Ingredients(True, True, False)
    assert treatment_ingredients(MODE_CUMULATIVE, "D") == Ingredients(True, True, True)


def test_singular_isolates_one_source_per_treatment():
    assert treatment_ingredients(MODE_SINGULAR, "A") == Ingredients(False, False, False)
    assert treatment_ingredients(MODE_SINGULAR, "B") == Ingredients(True, False, False)
    assert treatment_ingredients(MODE_SINGULAR, "C") == Ingredients(False, True, False)
    assert treatment_ingredients(MODE_SINGULAR, "D") == Ingredients(False, False, True)
    assert treatment_ingredients(MODE_SINGULAR, "E") == Ingredients(True, True, True)


def test_e_is_only_valid_in_singular_mode():
    assert "E" not in valid_treatments(MODE_CUMULATIVE)
    assert "E" in valid_treatments(MODE_SINGULAR)
    with pytest.raises(ValueError):
        treatment_ingredients(MODE_CUMULATIVE, "E")


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


def test_prompt_html_is_truncated_at_8000_chars():
    big = "x" * 9000
    _, user = build_assertion_prompt(
        _record(), variant_source="// TODO", include_comments=False, html_content=big,
    )
    assert "... truncated ..." in user
    assert big not in user
