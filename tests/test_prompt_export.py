from __future__ import annotations

import pandas as pd

from src.evaluation.prompt_export import export_llm_calls


def test_export_llm_calls_to_csv(tmp_path):
    output = tmp_path / "prompts.csv"
    export_llm_calls([
        {
            "id": 1,
            "call_type": "generation",
            "app": "mantisbt",
            "class_name": "AddNewProject",
            "method_name": "addNewProject",
            "treatment": "D",
            "model": "gpt-4o-mini",
            "provider": "openai",
            "system_prompt": "system",
            "user_prompt": "user",
            "raw_response": "raw",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cost_usd": 0.0012,
        }
    ], output)

    df = pd.read_csv(output)

    assert len(df) == 1
    assert df.loc[0, "call_type"] == "generation"
    assert df.loc[0, "system_prompt"] == "system"
    assert df.loc[0, "user_prompt"] == "user"
    assert df.loc[0, "raw_response"] == "raw"
    assert df.loc[0, "input_tokens"] == 100


def test_export_llm_calls_to_excel(tmp_path):
    output = tmp_path / "prompts.xlsx"
    export_llm_calls([
        {
            "id": 1,
            "call_type": "pre_classification",
            "app": "mantisbt",
            "class_name": "AddNewProject",
            "method_name": "addNewProject",
            "treatment": "D",
            "model": "gpt-4o-mini",
            "provider": "openai",
            "system_prompt": "system",
            "user_prompt": "user",
            "raw_response": "raw",
        }
    ], output)

    df = pd.read_excel(output, sheet_name="Prompts", engine="openpyxl")

    assert len(df) == 1
    assert df.loc[0, "call_type"] == "pre_classification"
    assert df.loc[0, "system_prompt"] == "system"
