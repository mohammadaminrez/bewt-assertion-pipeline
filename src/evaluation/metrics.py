from __future__ import annotations

"""Descriptive metrics and statistical tests for the treatment comparison.

The primary outcome is whether a generated assertion passes on the live app —
the execution harness that decides it is validated by positive/negative
controls. Textual similarity to the gold assertion is descriptive only: a
non-compiling answer can be textually very close to the gold.

Each (test, treatment, model) may be generated in several independent runs.
Tests are the unit of analysis: a test's pass rate under a treatment is the
fraction of runs in which it passed.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from statistics import mean, pstdev
from typing import Callable

from ..models import ExperimentResult

CATEGORIES = [
    "correct", "over_assertive", "under_assertive", "wrong_assertion",
    "not_executable", "needs_review",
]


@dataclass
class AggregateMetrics:
    total: int                      # generated answers (tests x runs)
    tests: int
    runs: int
    compiles: int
    passes: int
    exact_matches: int
    avg_textual_similarity: float
    pass_rate: float                # passes / total
    pass_rate_runs: list[float] = field(default_factory=list)  # one per run
    categories: dict[str, int] = field(default_factory=dict)   # final categories

    @property
    def pass_rate_sd(self) -> float:
        return pstdev(self.pass_rate_runs) if len(self.pass_rate_runs) > 1 else 0.0


def compute_aggregate_metrics(results: list[ExperimentResult]) -> AggregateMetrics:
    if not results:
        return AggregateMetrics(0, 0, 0, 0, 0, 0, 0.0, 0.0)
    by_run: dict[int, list[ExperimentResult]] = defaultdict(list)
    for r in results:
        by_run[r.run].append(r)
    categories = {c: 0 for c in CATEGORIES}
    for r in results:
        categories[r.final_category] = categories.get(r.final_category, 0) + 1
    return AggregateMetrics(
        total=len(results),
        tests=len({(r.test_record.app, r.test_record.class_name) for r in results}),
        runs=len(by_run),
        compiles=sum(r.compiles for r in results),
        passes=sum(r.passes for r in results),
        exact_matches=sum(r.exact_match for r in results),
        avg_textual_similarity=mean(r.semantic_similarity for r in results),
        pass_rate=sum(r.passes for r in results) / len(results),
        pass_rate_runs=[sum(r.passes for r in rs) / len(rs) for _, rs in sorted(by_run.items())],
        categories=categories,
    )


def compute_grouped_metrics(
    results: list[ExperimentResult],
    key: Callable[[ExperimentResult], tuple],
) -> dict[tuple, AggregateMetrics]:
    groups: dict[tuple, list[ExperimentResult]] = defaultdict(list)
    for r in results:
        groups[key(r)].append(r)
    return {k: compute_aggregate_metrics(v) for k, v in sorted(groups.items())}


# --- statistical tests ----------------------------------------------------------

def holm(p_values: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values."""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted, running = {}, 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[name] = running
    return adjusted


def _test_pass_rates(results: list[ExperimentResult]) -> dict[str, dict[tuple, float]]:
    """treatment -> {(app, class): fraction of runs passed}."""
    runs: dict[str, dict[tuple, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        runs[r.treatment][(r.test_record.app, r.test_record.class_name)].append(r.passes)
    return {t: {k: sum(v) / len(v) for k, v in tests.items()} for t, tests in runs.items()}


def _aligned(rates: dict[str, dict[tuple, float]], treatments: list[str]) -> list[tuple]:
    """Tests that have a result under every treatment (paired design)."""
    common = set.intersection(*(set(rates[t]) for t in treatments))
    return sorted(common)


def _wilcoxon(x: list[float], y: list[float]) -> dict:
    from scipy import stats

    diffs = [a - b for a, b in zip(x, y) if a != b]
    if not diffs:
        return {"statistic": None, "p": 1.0, "n_nonzero": 0, "rank_biserial": 0.0}
    res = stats.wilcoxon(x, y, zero_method="wilcox")
    ranks = stats.rankdata([abs(d) for d in diffs])
    positive = sum(rank for rank, d in zip(ranks, diffs) if d > 0)
    negative = sum(rank for rank, d in zip(ranks, diffs) if d < 0)
    return {
        "statistic": float(res.statistic),
        "p": float(res.pvalue),
        "n_nonzero": len(diffs),
        # matched-pairs rank-biserial correlation, in [-1, 1]; >0 means x > y
        "rank_biserial": (positive - negative) / (positive + negative),
    }


def _cochrans_q(table: list[list[int]]) -> dict:
    """Cochran's Q for k related binary samples (rows = tests, columns = treatments)."""
    from scipy import stats

    k = len(table[0])
    col = [sum(row[j] for row in table) for j in range(k)]
    row = [sum(r) for r in table]
    n = sum(row)
    denominator = k * n - sum(r * r for r in row)
    if denominator == 0:
        return {"Q": None, "df": k - 1, "p": 1.0}
    q = (k - 1) * (k * sum(c * c for c in col) - n * n) / denominator
    return {"Q": q, "df": k - 1, "p": float(stats.chi2.sf(q, k - 1))}


def _mcnemar_exact(x: list[int], y: list[int]) -> dict:
    from scipy import stats

    b = sum(1 for a, c in zip(x, y) if a and not c)
    c = sum(1 for a, d in zip(x, y) if d and not a)
    p = 1.0 if b + c == 0 else float(stats.binomtest(min(b, c), b + c, 0.5).pvalue)
    return {"x_only": b, "y_only": c, "p": p}


def _treatment_tests(results: list[ExperimentResult]) -> dict:
    from scipy import stats

    rates = _test_pass_rates(results)
    treatments = sorted(rates)
    if len(treatments) < 2:
        return {}
    tests = _aligned(rates, treatments)
    out: dict = {"tests": len(tests), "treatments": treatments}

    # Primary: per-test pass rate across runs (Friedman + pairwise Wilcoxon).
    columns = {t: [rates[t][k] for k in tests] for t in treatments}
    primary: dict = {}
    if len(treatments) >= 3:
        if all(len(set(row)) == 1 for row in zip(*columns.values())):
            primary["friedman"] = {"statistic": None, "p": 1.0}
        else:
            stat, p = stats.friedmanchisquare(*columns.values())
            primary["friedman"] = {"statistic": float(stat), "p": float(p)}
    pairs = {f"{a}_vs_{b}": _wilcoxon(columns[a], columns[b]) for a, b in combinations(treatments, 2)}
    adjusted = holm({name: res["p"] for name, res in pairs.items()})
    for name, res in pairs.items():
        res["p_holm"] = adjusted[name]
    primary["pairwise_wilcoxon"] = pairs
    out["primary_pass_rate"] = primary

    # Robustness: majority vote per test (passes in more than half the runs).
    votes = {t: [int(rates[t][k] > 0.5) for k in tests] for t in treatments}
    robust: dict = {}
    if len(treatments) >= 3:
        robust["cochrans_q"] = _cochrans_q([list(row) for row in zip(*votes.values())])
    pairs = {f"{a}_vs_{b}": _mcnemar_exact(votes[a], votes[b]) for a, b in combinations(treatments, 2)}
    adjusted = holm({name: res["p"] for name, res in pairs.items()})
    for name, res in pairs.items():
        res["p_holm"] = adjusted[name]
    robust["pairwise_mcnemar"] = pairs
    out["robustness_majority_vote"] = robust
    return out


def statistical_tests(results: list[ExperimentResult]) -> dict:
    """Treatment comparison per model, plus a per-treatment model comparison."""
    by_model: dict[str, list[ExperimentResult]] = defaultdict(list)
    for r in results:
        by_model[r.model].append(r)
    output: dict = {
        "outcome": "pass on the live app; unit = test; pass rate = fraction of runs passed",
        "models": {m: _treatment_tests(rs) for m, rs in sorted(by_model.items())},
    }

    models = sorted(by_model)
    if len(models) >= 2:
        comparison = {}
        rates = {m: _test_pass_rates(by_model[m]) for m in models}
        for a, b in combinations(models, 2):
            per_treatment = {}
            for t in sorted(set(rates[a]) & set(rates[b])):
                tests = sorted(set(rates[a][t]) & set(rates[b][t]))
                per_treatment[t] = _wilcoxon([rates[a][t][k] for k in tests], [rates[b][t][k] for k in tests])
            adjusted = holm({t: res["p"] for t, res in per_treatment.items()})
            for t, res in per_treatment.items():
                res["p_holm"] = adjusted[t]
            comparison[f"{a}_vs_{b}"] = per_treatment
        output["model_comparison"] = comparison
    return output
