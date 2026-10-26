from __future__ import annotations

"""Export experiment results to Excel for manual annotation and import back."""

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from ..models import ErrorCategory, ExperimentResult

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


REVIEW_OPTIONS = "correct,over_assertive,under_assertive,wrong_assertion"

REVIEW_GUIDE_ROWS = [
    ["Classification", "When to use (every answer here PASSED on the live app but differs from the gold)", "Example"],
    ["correct", "Checks the same thing as the gold (same logic, different syntax)", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: assertTrue(page.getAccessLevel(2).equals("updater"))'],
    ["over_assertive", "Checks MORE than the gold (extra assertions or a stricter check)", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: same + assertEquals("user001", page.getUsername(2))'],
    ["under_assertive", "Checks LESS than the gold (weaker; would pass when it should fail)", 'Gold: assertEquals("Create your own content", p.getFirstArticle())\nGenerated: assertTrue(p.firstArticle.getText().contains("Create your own content"))'],
    ["wrong_assertion", "Checks something else (different element, value or logic) that happens to hold", 'Gold: assertEquals("updater", page.getAccessLevel(2))\nGenerated: assertEquals("admin", page.getAccessLevel(1))'],
]


def review_groups(results: list[ExperimentResult]) -> list[dict]:
    """Unique answers awaiting manual review (passed but differ from the gold),
    one group per (app, test, answer) with every place the answer occurred."""
    from .comparator import answer_key

    groups: dict[tuple, dict] = {}
    for r in results:
        if r.final_category != ErrorCategory.NEEDS_REVIEW.value:
            continue
        key = answer_key(r.generated_assertion)
        group = groups.setdefault((r.test_record.app, r.test_record.class_name, key), {
            "app": r.test_record.app,
            "class_name": r.test_record.class_name,
            "answer_key": key,
            "gold": r.test_record.gold_standard,
            "answer": r.generated_assertion,
            "occurrences": [],
            "result": r,
        })
        group["occurrences"].append(f"{r.model}/{r.treatment}/run{r.run}")
    return [groups[k] for k in sorted(groups)]


def export_review_to_excel(
    groups: list[dict],
    output_path: Path,
    pre_classifications: dict[str, str] | None = None,
) -> None:
    """One row per unique answer to review; the label applies to every occurrence."""
    rows = [{
        "App": g["app"],
        "Test Class": g["class_name"],
        "Answer Key": g["answer_key"],
        "Gold Standard Assertion": g["gold"],
        "Generated Assertion (passed)": g["answer"],
        "Occurrences": len(g["occurrences"]),
        "Where (model/treatment/run)": ", ".join(sorted(g["occurrences"])),
        "LLM Pre-Classification": (pre_classifications or {}).get(
            f"{g['app']}|{g['class_name']}|{g['answer_key']}", ""),
        "Manual Classification": "",
        "Manual Notes": "",
    } for g in groups]
    columns = ["App", "Test Class", "Answer Key", "Gold Standard Assertion", "Generated Assertion (passed)",
               "Occurrences", "Where (model/treatment/run)", "LLM Pre-Classification",
               "Manual Classification", "Manual Notes"]
    df = pd.DataFrame(rows, columns=columns)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(str(output_path), index=False, sheet_name="Review", engine="openpyxl")

    wb = load_workbook(str(output_path))
    _apply_formatting(
        wb["Review"], len(df), classification_options=REVIEW_OPTIONS,
        col_widths={"App": 10, "Test Class": 24, "Answer Key": 14, "Gold Standard Assertion": 55,
                    "Generated Assertion (passed)": 55, "Occurrences": 11,
                    "Where (model/treatment/run)": 30, "LLM Pre-Classification": 20,
                    "Manual Classification": 20, "Manual Notes": 30},
    )
    _add_guide_sheet(wb, guide_rows=REVIEW_GUIDE_ROWS)
    wb.save(str(output_path))


def import_review_labels(excel_path: Path) -> list[dict]:
    """Read filled-in review labels; rejects labels outside REVIEW_OPTIONS."""
    df = pd.read_excel(str(excel_path), sheet_name="Review", engine="openpyxl", dtype=str)
    allowed = set(REVIEW_OPTIONS.split(","))
    labels, invalid = [], []
    for index, row in df.iterrows():
        label = row.get("Manual Classification")
        if pd.isna(label) or not str(label).strip():
            continue
        label = str(label).strip()
        if label not in allowed:
            invalid.append(f"row {index + 2}: {label!r}")
            continue
        notes = row.get("Manual Notes")
        labels.append({
            "app": row["App"],
            "class_name": row["Test Class"],
            "answer_key": row["Answer Key"],
            "label": label,
            "notes": "" if pd.isna(notes) else str(notes),
        })
    if invalid:
        raise ValueError("Invalid labels (allowed: " + REVIEW_OPTIONS + "): " + "; ".join(invalid))
    return labels


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
        rows.append({
            "App": r.test_record.app,
            "Test Class": r.test_record.class_name,
            "Method": r.test_record.method_name,
            "Treatment": r.treatment,
            "Model": r.model,
            "Action Trace": trace,
            "Crawljax Oracle (baseline)": (
                "invariants + DOM/screenshot state-equivalence vs the crawled state"
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
