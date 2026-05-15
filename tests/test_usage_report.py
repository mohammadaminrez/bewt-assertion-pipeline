from __future__ import annotations

import pandas as pd

from src.evaluation.usage_report import build_treatment_comparison_rows, export_treatment_comparison


def test_build_treatment_comparison_rows_pivots_treatments():
    rows = build_treatment_comparison_rows([
        {
            "app": "mantisbt",
            "class_name": "AddNewProject",
            "method_name": "addNewProject",
            "model": "gpt-4o-mini",
            "treatment": "A",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cost_usd": 0.001,
            "latency_ms": 100,
            "exact_match": 1,
            "semantic_similarity": 0.9,
            "error_category": "correct",
            "manual_error_category": "correct",
            "llm_preclassification": "correct",
            "compiles": 1,
            "passes": 1,
        },
        {
            "app": "mantisbt",
            "class_name": "AddNewProject",
            "method_name": "addNewProject",
            "model": "gpt-4o-mini",
            "treatment": "D",
            "input_tokens": 300,
            "output_tokens": 40,
            "total_tokens": 340,
            "cost_usd": 0.003,
            "latency_ms": 300,
            "exact_match": 0,
            "semantic_similarity": 0.7,
            "error_category": "under_assertive",
            "manual_error_category": "wrong_assertion",
            "llm_preclassification": "under_assertive",
            "compiles": 1,
            "passes": 0,
        },
    ])

    assert len(rows) == 1
    assert rows[0]["app"] == "mantisbt"
    assert rows[0]["A_input_tokens"] == 100
    assert rows[0]["A_error_category"] == "correct"
    assert rows[0]["A_manual_error_category"] == "correct"
    assert rows[0]["A_llm_preclassification"] == "correct"
    assert rows[0]["D_input_tokens"] == 300
    assert rows[0]["D_error_category"] == "under_assertive"
    assert rows[0]["D_manual_error_category"] == "wrong_assertion"
    assert rows[0]["D_llm_preclassification"] == "under_assertive"


def test_export_treatment_comparison_to_csv(tmp_path):
    output = tmp_path / "comparison.csv"
    export_treatment_comparison([
        {
            "app": "mantisbt",
            "class_name": "AddNewProject",
            "method_name": "addNewProject",
            "model": "gpt-4o-mini",
            "treatment": "A",
            "input_tokens": 100,
            "error_category": "correct",
            "manual_error_category": "correct",
            "llm_preclassification": "correct",
        }
    ], output)

    df = pd.read_csv(output)

    assert len(df) == 1
    assert df.loc[0, "app"] == "mantisbt"
    assert df.loc[0, "A_input_tokens"] == 100
    assert df.loc[0, "A_error_category"] == "correct"
    assert df.loc[0, "A_manual_error_category"] == "correct"
    assert df.loc[0, "A_llm_preclassification"] == "correct"
