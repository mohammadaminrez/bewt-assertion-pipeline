import csv
import sqlite3

from src.evaluation.figures import write_figures


def _db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE experiments (model TEXT, treatment TEXT, app TEXT, source TEXT, "
                "passes INTEGER, error_category TEXT, manual_error_category TEXT)")
    con.executemany("INSERT INTO experiments VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_figures_are_drawn_from_the_results_database(tmp_path):
    _db(tmp_path / "results.db", [
        ("gpt-4o-mini", "A", "bludit", "bewt", 0, "not_executable", None),
        ("gpt-4o-mini", "A", "bludit", "bewt", 1, "needs_review", "under_assertive"),
        ("gpt-4o-mini", "D", "bludit", "bewt", 1, "correct", None),
        ("gpt-4o-mini", "D", "bludit", "bewt", 1, "correct", None),
        ("gpt-4o-mini", "C", "bludit", "crawled", 1, "correct", None),   # not benchmark
    ])
    figures = write_figures(tmp_path, log=lambda _: None)
    for name in ("fig1_pass_rate", "fig2_categories"):
        for ext in ("png", "pdf", "csv"):
            assert (figures / f"{name}.{ext}").stat().st_size > 0
    rates = list(csv.DictReader(open(figures / "fig1_pass_rate.csv")))
    assert [(r["treatment"], r["answers"], r["passed"]) for r in rates] == [("A", "2", "1"), ("D", "2", "2")]
    cats = {r["treatment"]: r for r in csv.DictReader(open(figures / "fig2_categories.csv"))}
    assert cats["A"]["under_assertive"] == "1" and cats["A"]["not_executable"] == "1"   # manual label wins
    assert cats["D"]["correct"] == "2"


def test_no_figures_without_benchmark_results(tmp_path):
    assert write_figures(tmp_path, log=lambda _: None) is None
