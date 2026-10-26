from __future__ import annotations

"""Generate reports in CSV and LaTeX formats."""

import csv
import json
from pathlib import Path

from ..models import ExperimentResult
from .metrics import CATEGORIES, AggregateMetrics, compute_grouped_metrics, statistical_tests


def generate_full_report(results: list[ExperimentResult], output_dir: Path) -> None:
    """Generate all report files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    write_raw_csv(results, output_dir / "raw_results.csv")

    by_treatment = compute_grouped_metrics(results, lambda r: (r.model, r.treatment))
    _write_metrics_csv(by_treatment, ["model", "treatment"], output_dir / "by_treatment.csv")
    _write_metrics_csv(
        compute_grouped_metrics(results, lambda r: (r.model, r.test_record.app, r.treatment)),
        ["model", "app", "treatment"], output_dir / "by_app.csv",
    )
    _write_metrics_csv(
        compute_grouped_metrics(results, lambda r: (r.model,)), ["model"], output_dir / "by_model.csv",
    )

    stats = statistical_tests(results)
    (output_dir / "statistical_tests.json").write_text(json.dumps(stats, indent=2))
    write_latex_tables(by_treatment, stats, output_dir)


def write_crawled_comparison(
    results: list[ExperimentResult],
    output_dir: Path,
    oracle_results: dict[str, str] | None = None,
    manual_categories: dict[str, str] | None = None,
) -> None:
    """Write the LLM-vs-Crawljax-oracle comparison for crawled tests.

    How many LLM assertions compile / hold on the live app, and the
    meaningfulness category distribution (manual if available, else the LLM
    judge's suggestion). The Crawljax oracle suite's pass rate is included as
    the baseline row.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    crawled = [r for r in results if r.source == "crawled"]
    if not crawled:
        return

    categories = ["semantic", "structural", "wrong_assertion", "not_executable"]
    counts = {c: 0 for c in categories}
    counts["unlabeled"] = 0
    for r in crawled:
        key = f"{r.test_record.app}|{r.test_record.class_name}|{r.treatment}|{r.model}"
        # Execution decides failing answers; only one that holds needs a label.
        label = (manual_categories or {}).get(key) or r.error_category.value
        if label in counts:
            counts[label] += 1
        else:
            counts["unlabeled"] += 1
    rows = [{
        "oracle": "LLM assertions",
        "total": len(crawled),
        "compiles": sum(1 for r in crawled if r.compiles),
        "holds_on_live_app": sum(1 for r in crawled if r.passes),
        **counts,
    }]

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


def write_generated_suite_summary(manifest: dict, output_dir: Path) -> None:
    """One row per crawled test of the complete suite: how many of the LLM's
    assertions were checked and kept, plus a total row with the run of the
    whole suite on a fresh app."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = ["test", "method", "assertions_generated", "assertions_kept", "answer_held_whole"]
    with open(output_dir / "generated_suite.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + ["suite_passes_on_fresh_app"])
        writer.writeheader()
        for t in manifest["per_test"]:
            writer.writerow({"test": t["class"], "method": t["method"],
                             "assertions_generated": len(t["assertions"]),
                             "assertions_kept": t["kept"], "answer_held_whole": int(t["answer_holds"])})
        writer.writerow({"test": "TOTAL", "method": "",
                         "assertions_generated": manifest["assertions_generated"],
                         "assertions_kept": manifest["assertions_kept"],
                         "answer_held_whole": manifest["answers_holding_whole"],
                         "suite_passes_on_fresh_app": int(manifest["suite_run"]["passed"])})


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


def _write_metrics_csv(metrics: dict[tuple, AggregateMetrics], key_names: list[str], path: Path) -> None:
    fieldnames = key_names + [
        "tests", "runs", "answers", "pass_rate", "pass_rate_sd_runs", "pass_rate_min_run",
        "pass_rate_max_run", "compile_rate", "exact_rate", "avg_textual_similarity", *CATEGORIES,
    ]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for key, m in metrics.items():
            row = dict(zip(key_names, key))
            row.update({
                "tests": m.tests,
                "runs": m.runs,
                "answers": m.total,
                "pass_rate": f"{m.pass_rate:.4f}",
                "pass_rate_sd_runs": f"{m.pass_rate_sd:.4f}",
                "pass_rate_min_run": f"{min(m.pass_rate_runs):.4f}",
                "pass_rate_max_run": f"{max(m.pass_rate_runs):.4f}",
                "compile_rate": f"{m.compiles / m.total:.4f}",
                "exact_rate": f"{m.exact_matches / m.total:.4f}",
                "avg_textual_similarity": f"{m.avg_textual_similarity:.4f}",
            })
            row.update({c: m.categories.get(c, 0) for c in CATEGORIES})
            writer.writerow(row)


def _p(value: float | None) -> str:
    if value is None:
        return "--"
    return "$<$0.001" if value < 0.001 else f"{value:.3f}"


def write_latex_tables(by_treatment: dict[tuple, AggregateMetrics], stats: dict, output_dir: Path) -> None:
    """Generate LaTeX tables ready for thesis insertion."""
    latex = r"""\begin{table}[htbp]
\centering
\caption{Assertion generation per treatment (pass rate on the live application; mean $\pm$ SD across runs)}
\label{tab:treatment_comparison}
\begin{tabular}{ll|cccc}
\toprule
\textbf{Model} & \textbf{Treatment} & \textbf{Pass rate} & \textbf{Compiles} & \textbf{Exact} & \textbf{Textual sim.} \\
\midrule
"""
    for (model, treatment), m in by_treatment.items():
        latex += (
            f"{model} & {treatment} & {m.pass_rate * 100:.1f}\\% $\\pm$ {m.pass_rate_sd * 100:.1f} "
            f"& {m.compiles / m.total * 100:.1f}\\% & {m.exact_matches / m.total * 100:.1f}\\% "
            f"& {m.avg_textual_similarity:.3f} \\\\\n"
        )
    latex += "\\bottomrule\n\\end{tabular}\n\\end{table}\n"

    latex += r"""
\begin{table}[htbp]
\centering
\caption{Final category of the generated assertions (counts over all runs)}
\label{tab:error_distribution}
\begin{tabular}{ll|cccccc}
\toprule
\textbf{Model} & \textbf{Treatment} & \textbf{Correct} & \textbf{Over} & \textbf{Under} & \textbf{Wrong} & \textbf{N/E} & \textbf{Unreviewed} \\
\midrule
"""
    for (model, treatment), m in by_treatment.items():
        vals = " & ".join(str(m.categories.get(c, 0)) for c in CATEGORIES)
        latex += f"{model} & {treatment} & {vals} \\\\\n"
    latex += "\\bottomrule\n\\end{tabular}\n\\end{table}\n"

    latex += r"""
\begin{table}[htbp]
\centering
\caption{Pairwise treatment comparison of per-test pass rates (Wilcoxon signed-rank, Holm-adjusted)}
\label{tab:treatment_tests}
\begin{tabular}{ll|cccc}
\toprule
\textbf{Model} & \textbf{Pair} & \textbf{$n_{\neq}$} & \textbf{$r_{rb}$} & \textbf{$p$} & \textbf{$p_{Holm}$} \\
\midrule
"""
    for model, res in stats.get("models", {}).items():
        for pair, w in res.get("primary_pass_rate", {}).get("pairwise_wilcoxon", {}).items():
            latex += (
                f"{model} & {pair.replace('_vs_', ' vs ')} & {w['n_nonzero']} & {w['rank_biserial']:.2f} "
                f"& {_p(w['p'])} & {_p(w['p_holm'])} \\\\\n"
            )
    latex += "\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    (output_dir / "latex_tables.tex").write_text(latex)
