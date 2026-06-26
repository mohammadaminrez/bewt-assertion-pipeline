from __future__ import annotations

"""Research-oriented usage and treatment comparison exports."""

from pathlib import Path

import pandas as pd


TREATMENTS = ("A", "B", "C", "D")


BASE_COMPARISON_COLUMNS = [
    "app",
    "class_name",
    "method_name",
    "model",
]


def build_treatment_comparison_rows(records: list[dict]) -> list[dict]:
    """Pivot per-treatment experiment/usage records into one row per test/model."""
    grouped: dict[tuple[str, str, str, str], dict] = {}
    for record in records:
        key = (
            record["app"],
            record["class_name"],
            record["method_name"],
            record["model"],
        )
        row = grouped.setdefault(key, {
            "app": record["app"],
            "class_name": record["class_name"],
            "method_name": record["method_name"],
            "model": record["model"],
        })
        treatment = record["treatment"]
        prefix = f"{treatment}_"
        row[f"{prefix}input_tokens"] = record.get("input_tokens")
        row[f"{prefix}output_tokens"] = record.get("output_tokens")
        row[f"{prefix}total_tokens"] = record.get("total_tokens")
        row[f"{prefix}cost_usd"] = record.get("cost_usd")
        row[f"{prefix}latency_ms"] = record.get("latency_ms")
        row[f"{prefix}exact_match"] = record.get("exact_match")
        row[f"{prefix}semantic_similarity"] = record.get("semantic_similarity")
        row[f"{prefix}error_category"] = record.get("error_category")
        row[f"{prefix}manual_error_category"] = record.get("manual_error_category")
        row[f"{prefix}llm_preclassification"] = record.get("llm_preclassification")
        row[f"{prefix}compiles"] = record.get("compiles")
        row[f"{prefix}passes"] = record.get("passes")

    return [grouped[key] for key in sorted(grouped)]


def treatment_comparison_columns() -> list[str]:
    columns = list(BASE_COMPARISON_COLUMNS)
    for treatment in TREATMENTS:
        prefix = f"{treatment}_"
        columns.extend([
            f"{prefix}input_tokens",
            f"{prefix}output_tokens",
            f"{prefix}total_tokens",
            f"{prefix}cost_usd",
            f"{prefix}latency_ms",
            f"{prefix}exact_match",
            f"{prefix}semantic_similarity",
            f"{prefix}error_category",
            f"{prefix}manual_error_category",
            f"{prefix}llm_preclassification",
            f"{prefix}compiles",
            f"{prefix}passes",
        ])
    return columns


def export_treatment_comparison(records: list[dict], output_path: Path) -> None:
    """Export one-row-per-test treatment comparison to .csv or .xlsx."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_treatment_comparison_rows(records)
    columns = treatment_comparison_columns()
    df = pd.DataFrame(rows, columns=columns)

    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(output_path, index=False)
    elif suffix in {".xlsx", ".xlsm"}:
        df.to_excel(output_path, index=False, sheet_name="Treatment Comparison", engine="openpyxl")
    else:
        raise ValueError("Treatment comparison output must end with .csv or .xlsx")
