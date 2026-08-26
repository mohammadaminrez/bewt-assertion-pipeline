from __future__ import annotations

"""Generate reports in CSV and LaTeX formats."""

import csv
import json
from pathlib import Path

from ..models import ExperimentResult
from .metrics import (
    AggregateMetrics,
    compute_aggregate_metrics,
    compute_per_app_metrics,
    compute_per_treatment_metrics,
    compute_per_model_metrics,
    statistical_tests,
)


def generate_full_report(results: list[ExperimentResult], output_dir: Path) -> None:
    """Generate all report files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Raw results CSV
    write_raw_csv(results, output_dir / "raw_results.csv")

    # Aggregate tables
    write_treatment_table(results, output_dir)
    write_app_table(results, output_dir)
    write_model_table(results, output_dir)

    # Statistical tests
    stats = statistical_tests(results)
    (output_dir / "statistical_tests.json").write_text(json.dumps(stats, indent=2))

    # LaTeX tables
    write_latex_tables(results, output_dir)



def write_crawled_comparison(
    results: list[ExperimentResult],
    output_dir: Path,
    oracle_results: dict[str, str] | None = None,
    manual_categories: dict[str, str] | None = None,
) -> None:
    """Write the LLM-vs-Crawljax-oracle comparison for crawled tests.

    Per treatment: how many LLM assertions compile / hold on the live app, and
    the meaningfulness category distribution (manual if available, else the
    LLM judge's suggestion). The Crawljax oracle suite's pass rate is included
    as the baseline row.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    crawled = [r for r in results if r.source == "crawled"]
    if not crawled:
        return

    categories = ["semantic", "structural", "wrong_assertion", "not_executable"]
    by_treatment: dict[str, list[ExperimentResult]] = {}
    for r in crawled:
        by_treatment.setdefault(r.treatment, []).append(r)

    rows = []
    for treatment in sorted(by_treatment):
        rs = by_treatment[treatment]
        counts = {c: 0 for c in categories}
        counts["unlabeled"] = 0
        for r in rs:
            key = f"{r.test_record.app}|{r.test_record.class_name}|{r.treatment}|{r.model}"
            label = (manual_categories or {}).get(key, "")
            if label in counts:
                counts[label] += 1
            else:
                counts["unlabeled"] += 1
        rows.append({
            "oracle": f"LLM assertions (treatment {treatment})",
            "total": len(rs),
            "compiles": sum(1 for r in rs if r.compiles),
            "holds_on_live_app": sum(1 for r in rs if r.passes),
            **counts,
        })

    if oracle_results:
        passed = sum(1 for s in oracle_results.values() if s == "PASS")
        rows.append({
            "oracle": "Crawljax oracle suite (invariants + state-equivalence)",
            "total": len(oracle_results),
            "compiles": len(oracle_results),
            "holds_on_live_app": passed,
            **{c: "" for c in categories},
            "unlabeled": "",
        })

    fieldnames = ["oracle", "total", "compiles", "holds_on_live_app",
                  *categories, "unlabeled"]
    with open(output_dir / "crawled_comparison.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_raw_csv(results: list[ExperimentResult], path: Path) -> None:
    """Write raw results to CSV."""
    if not results:
        return

    fieldnames = list(results[0].to_dict().keys())
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_dict())


def write_treatment_table(results: list[ExperimentResult], output_dir: Path) -> None:
    """Write per-treatment comparison table."""
    metrics = compute_per_treatment_metrics(results)
    _write_metrics_csv(metrics, output_dir / "by_treatment.csv")


def write_app_table(results: list[ExperimentResult], output_dir: Path) -> None:
    """Write per-app comparison table."""
    metrics = compute_per_app_metrics(results)
    _write_metrics_csv(metrics, output_dir / "by_app.csv")


def write_model_table(results: list[ExperimentResult], output_dir: Path) -> None:
    """Write per-model comparison table."""
    metrics = compute_per_model_metrics(results)
    _write_metrics_csv(metrics, output_dir / "by_model.csv")


def _write_metrics_csv(metrics: dict[str, AggregateMetrics], path: Path) -> None:
    """Write an AggregateMetrics dict to CSV."""
    fieldnames = [
        "group", "total", "compiles", "passes", "exact_matches",
        "avg_semantic_similarity", "precision", "recall", "f1_score",
        "correct", "over_assertive", "under_assertive", "wrong_assertion", "not_executable",
    ]

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for group_name, m in sorted(metrics.items()):
            row = {
                "group": group_name,
                "total": m.total,
                "compiles": m.compiles,
                "passes": m.passes,
                "exact_matches": m.exact_matches,
                "avg_semantic_similarity": f"{m.avg_semantic_similarity:.4f}",
                "precision": f"{m.precision:.4f}",
                "recall": f"{m.recall:.4f}",
                "f1_score": f"{m.f1_score:.4f}",
            }
            for cat in ["correct", "over_assertive", "under_assertive", "wrong_assertion", "not_executable"]:
                row[cat] = m.error_distribution.get(cat, 0)
            writer.writerow(row)


def write_latex_tables(results: list[ExperimentResult], output_dir: Path) -> None:
    """Generate LaTeX tables ready for thesis insertion."""
    treatment_metrics = compute_per_treatment_metrics(results)

    latex = r"""\begin{table}[htbp]
\centering
\caption{Comparison of assertion generation across treatments}
\label{tab:treatment_comparison}
\begin{tabular}{l|ccc|ccc|c}
\toprule
\textbf{Treatment} & \textbf{Compiles} & \textbf{Passes} & \textbf{Exact} & \textbf{P} & \textbf{R} & \textbf{F1} & \textbf{Sim.} \\
\midrule
"""

    for treatment in sorted(treatment_metrics.keys()):
        m = treatment_metrics[treatment]
        compile_pct = m.compiles / m.total * 100 if m.total > 0 else 0
        pass_pct = m.passes / m.total * 100 if m.total > 0 else 0
        exact_pct = m.exact_matches / m.total * 100 if m.total > 0 else 0

        latex += (
            f"{treatment} & {compile_pct:.1f}\\% & {pass_pct:.1f}\\% & {exact_pct:.1f}\\% "
            f"& {m.precision:.3f} & {m.recall:.3f} & {m.f1_score:.3f} "
            f"& {m.avg_semantic_similarity:.3f} \\\\\n"
        )

    latex += r"""\bottomrule
\end{tabular}
\end{table}
"""

    # Error distribution table
    latex += r"""
\begin{table}[htbp]
\centering
\caption{Error type distribution across treatments}
\label{tab:error_distribution}
\begin{tabular}{l|ccccc}
\toprule
\textbf{Treatment} & \textbf{Correct} & \textbf{Over} & \textbf{Under} & \textbf{Wrong} & \textbf{N/E} \\
\midrule
"""

    for treatment in sorted(treatment_metrics.keys()):
        m = treatment_metrics[treatment]
        cats = ["correct", "over_assertive", "under_assertive", "wrong_assertion", "not_executable"]
        vals = [str(m.error_distribution.get(c, 0)) for c in cats]
        latex += f"{treatment} & {' & '.join(vals)} \\\\\n"

    latex += r"""\bottomrule
\end{tabular}
\end{table}
"""

    (output_dir / "latex_tables.tex").write_text(latex)
