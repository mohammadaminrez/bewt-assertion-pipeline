from __future__ import annotations

import pandas as pd

from src.evaluation.excel_export import import_classifications_from_excel


def _write_sheet(path, rows):
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name="Results", engine="openpyxl")


def test_import_classifications_reads_reviewed_labels(tmp_path):
    path = tmp_path / "manual.xlsx"
    _write_sheet(path, [{
        "App": "mantisbt",
        "Mode": "cumulative",
        "Test Class": "AddNewProject",
        "Treatment": "D",
        "Model": "gpt-4o-mini",
        "Classification": "wrong_assertion",
        "Notes": "checks the wrong field",
    }])

    annotations = import_classifications_from_excel(path)

    assert annotations == [{
        "app": "mantisbt",
        "mode": "cumulative",
        "class_name": "AddNewProject",
        "treatment": "D",
        "model": "gpt-4o-mini",
        "manual_classification": "wrong_assertion",
        "manual_notes": "checks the wrong field",
    }]


def test_import_skips_rows_that_have_not_been_reviewed(tmp_path):
    """A partially reviewed sheet must import cleanly."""
    path = tmp_path / "manual.xlsx"
    _write_sheet(path, [
        {
            "App": "mantisbt", "Mode": "cumulative", "Test Class": "Reviewed",
            "Treatment": "A", "Model": "gpt-4o", "Classification": "correct", "Notes": "",
        },
        {
            "App": "mantisbt", "Mode": "cumulative", "Test Class": "NotReviewed",
            "Treatment": "B", "Model": "gpt-4o", "Classification": "", "Notes": "",
        },
    ])

    annotations = import_classifications_from_excel(path)

    assert [a["class_name"] for a in annotations] == ["Reviewed"]
