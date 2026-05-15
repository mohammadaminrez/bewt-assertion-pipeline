from __future__ import annotations

"""Export logged LLM prompt/response traces for research review."""

from pathlib import Path

import pandas as pd


PROMPT_EXPORT_COLUMNS = [
    "id",
    "experiment_id",
    "call_type",
    "app",
    "class_name",
    "method_name",
    "treatment",
    "model",
    "provider",
    "prompt_hash",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "reasoning_tokens",
    "cost_usd",
    "latency_ms",
    "created_at",
    "system_prompt",
    "user_prompt",
    "raw_response",
]


def export_llm_calls(calls: list[dict], output_path: Path) -> None:
    """Export LLM call traces to .csv or .xlsx."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{column: call.get(column) for column in PROMPT_EXPORT_COLUMNS} for call in calls]
    df = pd.DataFrame(rows, columns=PROMPT_EXPORT_COLUMNS)

    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(output_path, index=False)
    elif suffix in {".xlsx", ".xlsm"}:
        df.to_excel(output_path, index=False, sheet_name="Prompts", engine="openpyxl")
    else:
        raise ValueError("Prompt export output must end with .csv or .xlsx")
