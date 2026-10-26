from __future__ import annotations

from src.execution.controls import negate
from src.execution.java_injector import ensure_assert_imports, fill_placeholders, localize_base_url
from src.llm.response_parser import (
    extract_assertion_from_response,
    has_extra_statements,
    validate_assertion,
)
from src.models import GherkinScenario
from src.parsing.java_parser import PLACEHOLDER, assign_blocks, extract_assertions, strip_assertions
from src.variants.generator import generate_variant_b, gherkin_comment

SPLIT_TEST = """package tests;

import static org.junit.Assert.assertEquals;

public class AddAsDraftTest extends BaseTest {
\t@Test
\tpublic void addAsDraft() {
\t\tContentPage content = new AdminLogin(driver).login();
\t\tassertFalse(content.getFirstContentTitle().contains("Draft Content"));
\t\tDraftPage drafts = content.drafts();
\t\tassertEquals("Draft Content", drafts.getFirstDraftTitle());

\t}
}"""

SINGLE_TEST = """package tests;

public class AddCategoryTest extends BaseTest {
\t@Test
\tpublic void addCategory() {
\t\tCategoriesPage categories = new AdminLogin(driver).login().categories();
\t\tassertEquals("Category001",
\t\t\t\tcategories.getFirstCategoryName());

\t\tassertTrue(categories.isShown());
\t}
}"""


def _record(source, name):
    from src.models import TestRecord
    assertions = extract_assertions(source)
    assign_blocks(source, assertions)
    return TestRecord(app="bludit", variant="baseline", version="3.13.1", file_path=f"{name}.java",
                      class_name=name, method_name="m", assertions=assertions,
                      full_source=source, stripped_source=strip_assertions(source, assertions))


def test_code_between_assertions_gives_numbered_placeholders():
    record = _record(SPLIT_TEST, "AddAsDraftTest")
    assert [a.block for a in record.assertions] == [0, 1]
    lines = record.stripped_source.split("\n")
    assert f"\t\t{PLACEHOLDER} (1)" in lines
    assert f"\t\t{PLACEHOLDER} (2)" in lines
    # the in-between code stays between the two placeholders
    first = lines.index(f"\t\t{PLACEHOLDER} (1)")
    assert lines[first + 1] == "\t\tDraftPage drafts = content.drafts();"
    assert lines[first + 2] == f"\t\t{PLACEHOLDER} (2)"
    assert record.gold_standard.split("\n")[0] == "// (1)"


def test_contiguous_assertions_share_one_plain_placeholder():
    record = _record(SINGLE_TEST, "AddCategoryTest")
    assert [a.block for a in record.assertions] == [0, 0]
    assert record.stripped_source.count(PLACEHOLDER) == 1
    assert f"\t\t{PLACEHOLDER}" in record.stripped_source.split("\n")
    assert "// (1)" not in record.gold_standard


def test_gold_round_trip_restores_every_assertion():
    for source, name in ((SPLIT_TEST, "AddAsDraftTest"), (SINGLE_TEST, "AddCategoryTest")):
        record = _record(source, name)
        filled, note = fill_placeholders(record.stripped_source, record.gold_standard)
        assert note == ""
        # identical code; continuation lines may only gain indentation
        assert [" ".join(a.full_text.split()) for a in extract_assertions(filled)] == \
            [" ".join(a.full_text.split()) for a in record.assertions]


def test_answer_without_markers_is_placed_at_first_placeholder_and_noted():
    record = _record(SPLIT_TEST, "AddAsDraftTest")
    filled, note = fill_placeholders(record.stripped_source, 'assertTrue(x.ok());')
    assert "no // (n) markers" in note
    assert "no answer for placeholder(s) [2]" in note
    assert PLACEHOLDER not in filled


def test_missing_gherkin_gives_no_comment():
    record = _record(SINGLE_TEST, "AddCategoryTest")
    assert gherkin_comment(record, {}) is None
    assert generate_variant_b(record, {}) == record.stripped_source


def test_gherkin_comment_goes_above_first_placeholder_with_its_indent():
    record = _record(SPLIT_TEST, "AddAsDraftTest")
    gmap = {"addasdrafttest": GherkinScenario("f", "s", ['"Draft Content" is shown in DRAFT'])}
    lines = generate_variant_b(record, gmap).split("\n")
    i = lines.index(f"\t\t{PLACEHOLDER} (1)")
    assert lines[i - 1] == '\t\t// Assert that "Draft Content" is shown in DRAFT'


def test_reply_code_is_kept_as_written():
    reply = ("Here is the assertion:\n```java\nDraftPage d = content.drafts();\n"
             "assertEquals(\n    \"Draft Content\",\n    d.getFirstDraftTitle()\n);\n```\nThis checks it.")
    code = extract_assertion_from_response(reply)
    assert code == ('DraftPage d = content.drafts();\nassertEquals(\n    "Draft Content",\n'
                    '    d.getFirstDraftTitle()\n);')
    assert validate_assertion(code)
    assert has_extra_statements(code)


def test_reply_with_semicolon_inside_string_is_not_cut():
    code = extract_assertion_from_response('assertEquals("a);b", page.text());')
    assert code == 'assertEquals("a);b", page.text());'
    assert not has_extra_statements(code)


def test_reply_markers_and_comments_are_not_extra_statements():
    code = "// (1)\nassertFalse(a.b());\n// (2)\n// the draft list\nassertEquals(\"x\", c.d());"
    assert not has_extra_statements(code)


def test_reply_without_assertion_is_invalid():
    assert not validate_assertion(extract_assertion_from_response("I cannot determine this."))


def test_missing_static_imports_are_added():
    src = "package tests;\n\nimport org.junit.Test;\n\nclass X { void m() { assertNotEquals(1, 2); assertEquals(1, 1); } }"
    out = ensure_assert_imports(src)
    assert "import static org.junit.Assert.assertNotEquals;" in out
    assert "import static org.junit.Assert.assertEquals;" in out


def test_negation_inverts_every_assertion():
    gold = '// (1)\nassertFalse(c.contains("x"));\n// (2)\nassertEquals("x", d.get());'
    assert negate(gold) == '// (1)\nassertTrue(c.contains("x"));\n// (2)\nassertNotEquals("x", d.get());'


def test_lan_ip_is_localized(tmp_path):
    base = tmp_path / "src" / "test" / "java" / "tests" / "BaseTest.java"
    base.parent.mkdir(parents=True)
    base.write_text('String app_url = "http://192.168.1.141:8080/admin";')
    assert localize_base_url(tmp_path, "http://localhost:8080") == [base]
    assert base.read_text() == 'String app_url = "http://localhost:8080/admin";'
