from pathlib import Path

import pandas as pd

from src.evaluation.excel_export import export_crawled_results_to_excel, import_classifications_from_excel
from src.evaluation.reporter import write_crawled_comparison
from src.models import ErrorCategory, ExperimentResult, TestRecord


def _crawled_result(class_name="CrawledBluditTest0", treatment="C", passes=True):
    record = TestRecord(
        app="bludit", variant="crawled", version="3.13.1",
        file_path="", class_name=class_name, method_name="test0", assertions=[],
    )
    return ExperimentResult(
        test_record=record, treatment=treatment, model="gpt-4o",
        source="crawled",
        prompt="p", raw_response="r",
        generated_assertion='assertTrue(driver.getPageSource().contains("Hello"));',
        compiles=True, passes=passes,
    )


STATE_MAP = {
    "CrawledBluditTest0": {
        "method": "test0",
        "trace_comment": '// The test clicks the element with id "new-content"; then clicks the element with id "save".\n// Verify the state of the page reached at this point.',
    }
}


def test_export_crawled_excel_side_by_side(tmp_path):
    out = tmp_path / "crawled.xlsx"
    export_crawled_results_to_excel(
        [_crawled_result()], out, STATE_MAP,
        pre_classifications={"bludit|CrawledBluditTest0|C|gpt-4o": "semantic"},
        oracle_results={"method_0": "PASS", "method_1": "FAIL"},
    )
    df = pd.read_excel(out, sheet_name="Results")
    assert len(df) == 1
    row = df.iloc[0]
    assert "state-equivalence vs the crawled state" in row["Crawljax Oracle (baseline)"]
    assert "1/2 methods pass" in row["Crawljax Oracle (baseline)"]
    assert row["LLM Pre-Classification"] == "semantic"
    assert "new-content" in row["Action Trace"]
    assert pd.isna(row["Manual Classification"]) or row["Manual Classification"] == ""


def test_write_crawled_comparison_csv(tmp_path):
    results = [
        _crawled_result(class_name="CrawledBluditTest0", passes=True),
        _crawled_result(class_name="CrawledBluditTest1", passes=False),
    ]
    write_crawled_comparison(
        results, tmp_path,
        oracle_results={"method_0": "PASS", "method_1": "FAIL"},
        manual_categories={"bludit|CrawledBluditTest0|C|gpt-4o": "semantic"},
    )
    df = pd.read_csv(tmp_path / "crawled_comparison.csv")
    assert len(df) == 2  # LLM assertions + oracle baseline row
    llm = df[df["oracle"] == "LLM assertions"].iloc[0]
    assert llm["total"] == 2
    assert llm["holds_on_live_app"] == 1
    assert llm["semantic"] == 1
    oracle_row = df[df["oracle"].str.startswith("Crawljax oracle")].iloc[0]
    assert oracle_row["holds_on_live_app"] == 1


def test_write_crawled_comparison_counts_execution_categories(tmp_path):
    wrong = _crawled_result(class_name="CrawledBluditTest0", passes=False)
    wrong.error_category = ErrorCategory.WRONG_ASSERTION
    broken = _crawled_result(class_name="CrawledBluditTest1", passes=False)
    broken.error_category = ErrorCategory.NOT_EXECUTABLE
    waiting = _crawled_result(class_name="CrawledBluditTest2", passes=True)
    waiting.error_category = ErrorCategory.NEEDS_REVIEW
    write_crawled_comparison([wrong, broken, waiting], tmp_path, manual_categories={})
    llm = pd.read_csv(tmp_path / "crawled_comparison.csv").iloc[0]
    assert (llm["wrong_assertion"], llm["not_executable"], llm["unlabeled"]) == (1, 1, 1)


def test_write_crawled_comparison_ignores_bewt_results(tmp_path):
    bewt = _crawled_result()
    bewt.source = "bewt"
    write_crawled_comparison([bewt], tmp_path)
    assert not (tmp_path / "crawled_comparison.csv").exists()


def test_crawled_excel_round_trip_without_treatment_column(tmp_path):
    out = tmp_path / "crawled.xlsx"
    export_crawled_results_to_excel([_crawled_result()], out, STATE_MAP)
    df = pd.read_excel(out, sheet_name="Results")
    assert "Treatment" not in df.columns
    df["Manual Classification"] = df["Manual Classification"].astype(object)
    df.loc[0, "Manual Classification"] = "semantic"
    df.to_excel(out, index=False, sheet_name="Results")

    [annotation] = import_classifications_from_excel(out)
    assert annotation["treatment"] == "C"
    assert annotation["manual_classification"] == "semantic"
