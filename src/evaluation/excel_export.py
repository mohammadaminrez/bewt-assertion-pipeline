from __future__ import annotations

"""Export experiment results to Excel for manual annotation and import back."""

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from ..models import ExperimentResult

CLASSIFICATION_OPTIONS = "correct,over_assertive,under_assertive,wrong_assertion,not_executable"

# Crawled tests have no gold standard; classification is a meaningfulness
# rubric relative to Crawljax's generic state-equivalence oracle.
CRAWLED_CLASSIFICATION_OPTIONS = "semantic,structural,wrong_assertion,not_executable"

CRAWLED_GUIDE_ROWS = [
    ["Classification", "When to use", "Example"],
    ["semantic", "Asserts a meaningful, user-visible outcome of the performed actions — more expressive than whole-DOM equality", 'After creating a post titled "Hello":\nassertTrue(driver.getPageSource().contains("Hello"))'],
    ["structural", "Executable and consistent with the page, but asserts an incidental/generic detail — no more meaningful than a DOM diff", 'assertTrue(driver.findElements(By.tagName("body")).size() > 0)'],
    ["wrong_assertion", "Asserts something inconsistent with the reached state or unrelated to the actions", 'Asserts a login error message after a successful save'],
    ["not_executable", "Does not compile or references elements/variables not in scope", "Uses an undeclared page object variable"],
]

GUIDE_ROWS = [
    ["Classification", "When to use", "Example"],
    ["correct", "Generated checks the same thing as gold (same logic, maybe different syntax)", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: assertTrue(page.getAccessLevel(2).equals("updater"))'],
    ["over_assertive", "Generated checks MORE than the gold (extra assertions or stricter checks)", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: same + assertEquals("user001", page.getUsername(2))'],
    ["under_assertive", "Generated checks LESS than the gold (weaker, would pass when it should fail)", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: assertTrue(driver.getPageSource().contains("updater"))'],
    ["wrong_assertion", "Generated checks the WRONG thing (different element, value, or logic)", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: assertEquals("admin", page.getAccessLevel(1))'],
    ["not_executable", "Does not compile or cannot run", "Syntax error, missing method, wrong locator"],
]


def export_results_to_excel(
    results: list[ExperimentResult],
    output_path: Path,
    pre_classifications: dict[str, str] | None = None,
) -> None:
    """Export results to an Excel file for manual annotation.

    Args:
        results: Experiment results to export.
        output_path: Path to write the .xlsx file.
        pre_classifications: Optional dict of "app|class|treatment|model" -> classification
            from LLM pre-classification.
    """
    rows = []
    for r in results:
        key = f"{r.test_record.app}|{r.test_record.class_name}|{r.treatment}|{r.model}"
        pre_class = (pre_classifications or {}).get(key, "")
        rows.append({
            "App": r.test_record.app,
            "Test Class": r.test_record.class_name,
            "Method": r.test_record.method_name,
            "Treatment": r.treatment,
            "Model": r.model,
            "Gold Standard Assertion": r.test_record.gold_standard,
            "Generated Assertion": r.generated_assertion,
            "Compiles": r.compiles,
            "Pass/Fail": "PASS" if r.passes else "FAIL",
            "Fail Reason": r.notes if not r.passes and r.notes and r.notes != "execution skipped" else "",
            "Semantic Sim (heuristic)": round(r.semantic_similarity, 4),
            "Auto-Classification": r.error_category.value,
            "LLM Pre-Classification": pre_class,
            "Manual Classification": "",
            "Manual Notes": "",
        })

    df = pd.DataFrame(rows)
    df.sort_values(by=["App", "Test Class", "Treatment", "Model"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_path), index=False, sheet_name="Results", engine="openpyxl")

    wb = load_workbook(str(output_path))
    ws = wb["Results"]

    _apply_formatting(ws, len(df))
    _add_guide_sheet(wb)

    wb.save(str(output_path))


def export_crawled_results_to_excel(
    results: list[ExperimentResult],
    output_path: Path,
    state_map: dict[str, dict],
    pre_classifications: dict[str, str] | None = None,
    oracle_results: dict[str, str] | None = None,
) -> None:
    """Export crawled-test results for manual meaningfulness annotation.

    One row per (test, treatment, model), with the LLM assertion side by side
    with a description of the Crawljax oracle for the same point, so the
    annotator judges expressiveness relative to that baseline.

    Args:
        state_map: ingestion state_map.json content (trace + final state per class).
        pre_classifications: "app|class|treatment|model" -> rubric category.
        oracle_results: optional oracle-suite outcome per test method
            (e.g. {"method_0": "PASS"}), shown for context.
    """
    oracle_summary = ""
    if oracle_results:
        passed = sum(1 for s in oracle_results.values() if s == "PASS")
        oracle_summary = f" (oracle suite: {passed}/{len(oracle_results)} methods pass)"

    rows = []
    for r in results:
        key = f"{r.test_record.app}|{r.test_record.class_name}|{r.treatment}|{r.model}"
        entry = state_map.get(r.test_record.class_name, {})
        trace = entry.get("trace_comment", "").replace("// ", "")
        final_state = entry.get("final_state", "?")
        rows.append({
            "App": r.test_record.app,
            "Test Class": r.test_record.class_name,
            "Method": r.test_record.method_name,
            "Treatment": r.treatment,
            "Model": r.model,
            "Action Trace": trace,
            "Crawljax Oracle (baseline)": (
                f"invariants + DOM/screenshot state-equivalence vs {final_state}"
                + oracle_summary
            ),
            "LLM Assertion": r.generated_assertion,
            "Compiles": r.compiles,
            "Pass/Fail": "PASS" if r.passes else "FAIL",
            "Fail Reason": r.notes if not r.passes and r.notes else "",
            "LLM Pre-Classification": (pre_classifications or {}).get(key, ""),
            "Manual Classification": "",
            "Manual Notes": "",
        })

    df = pd.DataFrame(rows)
    df.sort_values(by=["App", "Test Class", "Treatment", "Model"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_path), index=False, sheet_name="Results", engine="openpyxl")

    wb = load_workbook(str(output_path))
    ws = wb["Results"]
    _apply_formatting(
        ws, len(df),
        classification_options=CRAWLED_CLASSIFICATION_OPTIONS,
        col_widths={
            "App": 12, "Test Class": 24, "Method": 12, "Treatment": 10,
            "Model": 15, "Action Trace": 60, "Crawljax Oracle (baseline)": 40,
            "LLM Assertion": 55, "Compiles": 10, "Pass/Fail": 10,
            "Fail Reason": 35, "LLM Pre-Classification": 20,
            "Manual Classification": 20, "Manual Notes": 30,
        },
    )
    _add_guide_sheet(wb, guide_rows=CRAWLED_GUIDE_ROWS)
    wb.save(str(output_path))


def _apply_formatting(ws, num_rows: int, classification_options: str = CLASSIFICATION_OPTIONS,
                      col_widths: dict[str, int] | None = None) -> None:
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    pass_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fail_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = thin_border

    pass_fail_col = None
    manual_class_col = None
    for idx, cell in enumerate(ws[1], 1):
        if cell.value == "Pass/Fail":
            pass_fail_col = idx
        if cell.value == "Manual Classification":
            manual_class_col = idx

    for row in range(2, num_rows + 2):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = thin_border
            cell.alignment = Alignment(wrap_text=True, vertical="top")

        if pass_fail_col:
            cell = ws.cell(row=row, column=pass_fail_col)
            if cell.value == "PASS":
                cell.fill = pass_fill
            elif cell.value == "FAIL":
                cell.fill = fail_fill

    if manual_class_col:
        dv = DataValidation(
            type="list",
            formula1=f'"{classification_options}"',
            allow_blank=True,
        )
        dv.error = "Please select a valid classification"
        dv.errorTitle = "Invalid classification"
        dv.prompt = "Select classification"
        dv.promptTitle = "Manual Classification"
        ws.add_data_validation(dv)
        for row in range(2, num_rows + 2):
            dv.add(ws.cell(row=row, column=manual_class_col))

    if col_widths is None:
        col_widths = {
            "App": 12, "Test Class": 25, "Method": 20,
            "Treatment": 10, "Model": 15,
            "Gold Standard Assertion": 50, "Generated Assertion": 50,
            "Compiles": 10, "Pass/Fail": 10, "Fail Reason": 40,
            "Semantic Sim (heuristic)": 15, "Auto-Classification": 22,
            "LLM Pre-Classification": 22, "Manual Classification": 22, "Manual Notes": 30,
        }
    for idx, cell in enumerate(ws[1], 1):
        width = col_widths.get(cell.value, 15)
        ws.column_dimensions[get_column_letter(idx)].width = width

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _add_guide_sheet(wb, guide_rows: list | None = None) -> None:
    if guide_rows is None:
        guide_rows = GUIDE_ROWS
    gs = wb.create_sheet("Classification Guide")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)

    for row_idx, row_data in enumerate(guide_rows, 1):
        for col_idx, value in enumerate(row_data, 1):
            cell = gs.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if row_idx == 1:
                cell.fill = header_fill
                cell.font = header_font

    gs.column_dimensions["A"].width = 20
    gs.column_dimensions["B"].width = 55
    gs.column_dimensions["C"].width = 65


def import_classifications_from_excel(
    excel_path: Path,
) -> list[dict]:
    """Read manual classifications from an annotated Excel file.

    Returns a list of dicts with keys: app, class_name, treatment, model, manual_classification, manual_notes.
    """
    df = pd.read_excel(str(excel_path), sheet_name="Results", engine="openpyxl")

    annotations = []
    for _, row in df.iterrows():
        manual_class = row.get("Manual Classification", "")
        if pd.isna(manual_class) or not str(manual_class).strip():
            continue
        annotations.append({
            "app": row["App"],
            "class_name": row["Test Class"],
            "treatment": row["Treatment"],
            "model": row["Model"],
            "manual_classification": str(manual_class).strip(),
            "manual_notes": str(row.get("Manual Notes", "") or ""),
            "llm_preclassification": str(row.get("LLM Pre-Classification", "") or ""),
        })

    return annotations
