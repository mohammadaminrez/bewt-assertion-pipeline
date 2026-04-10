from __future__ import annotations

"""Compute evaluation metrics across experiment results."""

from collections import Counter
from dataclasses import dataclass

from ..models import ErrorCategory, ExperimentResult


@dataclass
class AggregateMetrics:
    total: int
    compiles: int
    passes: int
    exact_matches: int
    avg_semantic_similarity: float
    error_distribution: dict[str, int]
    precision: float
    recall: float
    f1_score: float


def compute_aggregate_metrics(results: list[ExperimentResult]) -> AggregateMetrics:
    """Compute aggregate metrics over a set of experiment results."""
    if not results:
        return AggregateMetrics(0, 0, 0, 0, 0.0, {}, 0.0, 0.0, 0.0)

    total = len(results)
    compiles = sum(1 for r in results if r.compiles)
    passes = sum(1 for r in results if r.passes)
    exact_matches = sum(1 for r in results if r.exact_match)
    avg_sim = sum(r.semantic_similarity for r in results) / total

    error_dist = Counter(r.error_category.value for r in results)

    # Functional accuracy metrics
    tp = sum(1 for r in results if r.error_category == ErrorCategory.CORRECT)
    fp = sum(1 for r in results if r.passes and r.error_category != ErrorCategory.CORRECT)
    fn = sum(1 for r in results if not r.passes and r.error_category == ErrorCategory.CORRECT)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return AggregateMetrics(
        total=total,
        compiles=compiles,
        passes=passes,
        exact_matches=exact_matches,
        avg_semantic_similarity=avg_sim,
        error_distribution=dict(error_dist),
        precision=precision,
        recall=recall,
        f1_score=f1,
    )


def compute_per_app_metrics(
    results: list[ExperimentResult],
) -> dict[str, AggregateMetrics]:
    """Compute metrics grouped by app."""
    by_app: dict[str, list[ExperimentResult]] = {}
    for r in results:
        app = r.test_record.app
        by_app.setdefault(app, []).append(r)

    return {app: compute_aggregate_metrics(rs) for app, rs in by_app.items()}


def compute_per_treatment_metrics(
    results: list[ExperimentResult],
) -> dict[str, AggregateMetrics]:
    """Compute metrics grouped by treatment (A, B, C)."""
    by_treatment: dict[str, list[ExperimentResult]] = {}
    for r in results:
        by_treatment.setdefault(r.treatment, []).append(r)

    return {t: compute_aggregate_metrics(rs) for t, rs in by_treatment.items()}


def compute_per_model_metrics(
    results: list[ExperimentResult],
) -> dict[str, AggregateMetrics]:
    """Compute metrics grouped by model."""
    by_model: dict[str, list[ExperimentResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    return {m: compute_aggregate_metrics(rs) for m, rs in by_model.items()}


def statistical_tests(
    results: list[ExperimentResult],
) -> dict[str, dict]:
    """Run statistical tests comparing treatments A, B, C.

    Returns a dict with test names and their results.
    """
    from scipy import stats

    # Group by treatment, get semantic similarity scores
    by_treatment: dict[str, list[float]] = {}
    for r in results:
        by_treatment.setdefault(r.treatment, []).append(r.semantic_similarity)

    output = {}

    treatments = sorted(by_treatment.keys())
    if len(treatments) < 2:
        return output

    # Friedman test (for 3+ related conditions)
    if len(treatments) >= 3:
        # Align by test case
        by_test: dict[str, dict[str, float]] = {}
        for r in results:
            key = f"{r.test_record.app}_{r.test_record.class_name}"
            by_test.setdefault(key, {})[r.treatment] = r.semantic_similarity

        # Only include tests that have all three treatments
        aligned = {k: v for k, v in by_test.items() if len(v) == len(treatments)}
        if len(aligned) >= 3:
            groups = []
            for t in treatments:
                groups.append([aligned[k][t] for k in sorted(aligned.keys())])

            stat, p_value = stats.friedmanchisquare(*groups)
            output["friedman"] = {"statistic": stat, "p_value": p_value}

    # Pairwise Wilcoxon signed-rank tests
    for i in range(len(treatments)):
        for j in range(i + 1, len(treatments)):
            t1, t2 = treatments[i], treatments[j]

            # Align pairs
            by_test_pair: dict[str, dict[str, float]] = {}
            for r in results:
                if r.treatment in (t1, t2):
                    key = f"{r.test_record.app}_{r.test_record.class_name}"
                    by_test_pair.setdefault(key, {})[r.treatment] = r.semantic_similarity

            paired = {k: v for k, v in by_test_pair.items() if len(v) == 2}
            if len(paired) >= 5:
                x = [paired[k][t1] for k in sorted(paired.keys())]
                y = [paired[k][t2] for k in sorted(paired.keys())]

                try:
                    stat, p_value = stats.wilcoxon(x, y)
                    output[f"wilcoxon_{t1}_vs_{t2}"] = {
                        "statistic": stat,
                        "p_value": p_value,
                    }
                except ValueError:
                    pass

                # Cliff's delta
                delta = _cliffs_delta(x, y)
                output[f"cliffs_delta_{t1}_vs_{t2}"] = {"delta": delta}

    return output


def _cliffs_delta(x: list[float], y: list[float]) -> float:
    """Compute Cliff's delta effect size."""
    n_x = len(x)
    n_y = len(y)
    if n_x == 0 or n_y == 0:
        return 0.0

    more = sum(1 for xi in x for yi in y if xi > yi)
    less = sum(1 for xi in x for yi in y if xi < yi)
    return (more - less) / (n_x * n_y)
