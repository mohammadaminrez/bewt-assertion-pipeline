from __future__ import annotations

import pandas as pd

from src.evaluation.excel_export import import_classifications_from_excel


def test_import_classifications_reads_llm_preclassification_separately(tmp_path):
    path = tmp_path / "manual.xlsx"
    pd.DataFrame([{
        "App": "mantisbt",
        "Test Class": "AddNewProject",
        "Treatment": "D",
        "Model": "gpt-4o-mini",
        "LLM Pre-Classification": "under_assertive",
        "Manual Classification": "wrong_assertion",
        "Manual Notes": "manual correction",
    }]).to_excel(path, index=False, sheet_name="Results", engine="openpyxl")

    annotations = import_classifications_from_excel(path)

    assert annotations == [{
        "app": "mantisbt",
        "class_name": "AddNewProject",
        "treatment": "D",
        "model": "gpt-4o-mini",
        "manual_classification": "wrong_assertion",
        "manual_notes": "manual correction",
        "llm_preclassification": "under_assertive",
    }]
