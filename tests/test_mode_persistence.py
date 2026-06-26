from __future__ import annotations

import sqlite3

from src.data.store import ResultStore
from src.models import ExperimentResult, ErrorCategory, TestRecord


def _result(treatment: str, mode: str) -> ExperimentResult:
    rec = TestRecord(
        app="mantisbt", variant="base", version="2.25.4",
        file_path="X.java", class_name="AddCategoryTest", method_name="addCategory",
    )
    return ExperimentResult(
        test_record=rec, treatment=treatment, model="gpt-4o", mode=mode,
        prompt="p", raw_response="r", generated_assertion="assertTrue(x);",
        error_category=ErrorCategory.CORRECT,
    )


def test_same_treatment_coexists_across_modes(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result("C", "cumulative"))
    store.save_result(_result("C", "singular"))

    # Both rows are kept — mode is part of the uniqueness key.
    assert store.has_result("mantisbt", "2.25.4", "AddCategoryTest", "C", "gpt-4o", "cumulative")
    assert store.has_result("mantisbt", "2.25.4", "AddCategoryTest", "C", "gpt-4o", "singular")
    assert len(store.get_all_results()) == 2
    store.close()


def test_legacy_db_without_mode_is_migrated(tmp_path):
    db = tmp_path / "legacy.db"
    # Build a pre-mode experiments table and insert one row.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app TEXT NOT NULL, variant TEXT NOT NULL, version TEXT NOT NULL,
            class_name TEXT NOT NULL, method_name TEXT NOT NULL,
            treatment TEXT NOT NULL, model TEXT NOT NULL,
            prompt TEXT, raw_response TEXT, generated_assertion TEXT, gold_standard TEXT,
            compiles INTEGER DEFAULT 0, passes INTEGER DEFAULT 0, exact_match INTEGER DEFAULT 0,
            error_category TEXT, semantic_similarity REAL DEFAULT 0.0, notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(app, version, class_name, treatment, model)
        );
        INSERT INTO experiments (app, variant, version, class_name, method_name, treatment, model)
        VALUES ('mantisbt', 'base', '2.25.4', 'AddCategoryTest', 'addCategory', 'A', 'gpt-4o');
    """)
    conn.commit()
    conn.close()

    store = ResultStore(db)  # opening runs the migration
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(experiments)")}
    assert "mode" in cols
    # Legacy row is backfilled as the original cumulative design and preserved.
    assert store.has_result("mantisbt", "2.25.4", "AddCategoryTest", "A", "gpt-4o", "cumulative")
    assert len(store.get_all_results()) == 1
    store.close()
