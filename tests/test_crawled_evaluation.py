from pathlib import Path

import pandas as pd

from src.evaluation.excel_export import export_crawled_results_to_excel
from src.evaluation.reporter import write_crawled_comparison
from src.models import ExperimentResult, TestRecord


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
        "final_state": "state2",
        "dom_path": None,
        "trace_comment": '// The test clicks "New content"; then clicks "Save".\n// Verify the state (state2).',
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
    assert "state-equivalence vs state2" in row["Crawljax Oracle (baseline)"]
    assert "1/2 methods pass" in row["Crawljax Oracle (baseline)"]
    assert row["LLM Pre-Classification"] == "semantic"
    assert "New content" in row["Action Trace"]
    assert pd.isna(row["Manual Classification"]) or row["Manual Classification"] == ""


def test_write_crawled_comparison_csv(tmp_path):
    results = [
        _crawled_result(treatment="A", passes=False),
        _crawled_result(treatment="C", passes=True),
    ]
    write_crawled_comparison(
        results, tmp_path,
        oracle_results={"method_0": "PASS", "method_1": "FAIL"},
        manual_categories={"bludit|CrawledBluditTest0|C|gpt-4o": "semantic"},
    )
    df = pd.read_csv(tmp_path / "crawled_comparison.csv")
    assert len(df) == 3  # treatments A and C + oracle baseline row
    llm_c = df[df["oracle"] == "LLM assertions (treatment C)"].iloc[0]
    assert llm_c["holds_on_live_app"] == 1
    assert llm_c["semantic"] == 1
    oracle_row = df[df["oracle"].str.startswith("Crawljax oracle")].iloc[0]
    assert oracle_row["holds_on_live_app"] == 1


def test_write_crawled_comparison_ignores_bewt_results(tmp_path):
    bewt = _crawled_result()
    bewt.source = "bewt"
    write_crawled_comparison([bewt], tmp_path)
    assert not (tmp_path / "crawled_comparison.csv").exists()
