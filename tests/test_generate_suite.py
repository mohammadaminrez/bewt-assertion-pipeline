from src.execution.suite import SUITE_CLASS, _enriched_source, _units, split_statements

from tests.test_dante_ingest import SUITE_SOURCE


def test_split_statements_respects_strings_and_parentheses():
    code = (
        'assertEquals("a;b", driver.getTitle());\n'
        "// (1)\n"
        'assertTrue(driver.findElement(By.xpath("//h2[contains(., \'x;y\')]"))\n'
        "    .isDisplayed());"
    )
    assert split_statements(code) == [
        'assertEquals("a;b", driver.getTitle());',
        "assertTrue(driver.findElement(By.xpath(\"//h2[contains(., 'x;y')]\")) .isDisplayed());",
    ]


def test_answer_with_helper_statement_is_one_unit():
    answer = 'String t = driver.getTitle();\nassertEquals("Bludit", t);'
    assert _units(answer) == [answer]
    assert _units("// nothing to assert") == []


def test_enriched_source_appends_kept_assertions_in_crawl_order():
    source = _enriched_source(SUITE_SOURCE, {"test0": ['assertEquals("Bludit", driver.getTitle());']})
    assert f"public class {SUITE_CLASS}" in source
    assert "@FixMethodOrder(MethodSorters.NAME_ASCENDING)" in source
    assert "import static org.junit.Assert.assertEquals;" in source
    test0 = source[source.index("void test0()"):source.index("void test1()")]
    assert 'assertEquals("Bludit", driver.getTitle());' in test0
    test1 = source[source.index("void test1()"):]
    assert "assertEquals" not in test1


def test_generated_suite_summary_csv(tmp_path):
    import pandas as pd
    from src.evaluation.reporter import write_generated_suite_summary

    manifest = {
        "assertions_generated": 3, "assertions_kept": 2, "answers_holding_whole": 0,
        "suite_run": {"passed": True},
        "per_test": [
            {"class": "CrawledBluditTest00", "method": "test00", "answer_holds": False, "kept": 2,
             "assertions": [{"holds": True}, {"holds": True}, {"holds": False}]},
        ],
    }
    write_generated_suite_summary(manifest, tmp_path)
    df = pd.read_csv(tmp_path / "generated_suite.csv")
    assert list(df["test"]) == ["CrawledBluditTest00", "TOTAL"]
    total = df.iloc[-1]
    assert (total["assertions_generated"], total["assertions_kept"], total["suite_passes_on_fresh_app"]) == (3, 2, 1)
