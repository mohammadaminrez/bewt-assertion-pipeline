import sqlite3
from pathlib import Path

from src.data.store import ResultStore
from src.models import AssertionRecord, AssertionType, ErrorCategory, ExperimentResult, TestRecord
from src.llm.types import LLMCall


def _record(class_name="CrawledBluditTest0"):
    return TestRecord(
        app="bludit", variant="crawled", version="3.13.1",
        file_path="", class_name=class_name, method_name="test0",
        assertions=[],
    )


def _result(source="crawled", treatment="A", **kwargs):
    return ExperimentResult(
        test_record=_record(),
        treatment=treatment, model="gpt-4o", mode="cumulative", source=source,
        prompt="p", raw_response="r", generated_assertion="assertTrue(true);",
        **kwargs,
    )


def _call(source="crawled"):
    return LLMCall(
        call_type="generation", app="bludit", class_name="CrawledBluditTest0",
        method_name="test0", treatment="A", mode="cumulative", source=source,
        model="gpt-4o", provider="openai",
        system_prompt="s", user_prompt="u", raw_response="r",
    )


def test_same_key_different_source_do_not_collide(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    bewt_result = _result(source="bewt")
    crawled_result = _result(source="crawled")
    store.save_result(bewt_result)
    store.save_result(crawled_result)

    rows = store.get_all_results()
    assert len(rows) == 2
    assert {row["source"] for row in rows} == {"bewt", "crawled"}
    store.close()


def test_has_result_is_source_aware(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result(source="crawled"))

    assert store.has_result("bludit", "3.13.1", "CrawledBluditTest0", "A",
                            "gpt-4o", "cumulative", source="crawled")
    assert not store.has_result("bludit", "3.13.1", "CrawledBluditTest0", "A",
                                "gpt-4o", "cumulative", source="bewt")
    store.close()


def test_migration_backfills_existing_rows_as_bewt(tmp_path):
    db_path = tmp_path / "results.db"
    # Simulate a pre-source database (with mode, without source).
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app TEXT NOT NULL, variant TEXT NOT NULL, version TEXT NOT NULL,
            class_name TEXT NOT NULL, method_name TEXT NOT NULL,
            treatment TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'cumulative',
            model TEXT NOT NULL,
            prompt TEXT, raw_response TEXT, generated_assertion TEXT,
            gold_standard TEXT, compiles INTEGER DEFAULT 0, passes INTEGER DEFAULT 0,
            exact_match INTEGER DEFAULT 0, error_category TEXT,
            manual_error_category TEXT, manual_notes TEXT, llm_preclassification TEXT,
            semantic_similarity REAL DEFAULT 0.0, notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(app, version, class_name, treatment, model, mode)
        );
        INSERT INTO experiments (app, variant, version, class_name, method_name,
                                 treatment, mode, model)
        VALUES ('bludit', 'baseline', '3.13.1', 'AddContentTest', 'test', 'A',
                'cumulative', 'gpt-4o');
    """)
    conn.commit()
    conn.close()

    store = ResultStore(db_path)
    rows = store.get_all_results()
    assert len(rows) == 1
    assert rows[0]["source"] == "bewt"
    store.close()


def test_llm_calls_store_and_filter_by_source(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_llm_call(_call(source="crawled"))
    store.save_llm_call(_call(source="bewt"))

    crawled_calls = store.get_llm_calls(source="crawled")
    assert len(crawled_calls) == 1
    assert crawled_calls[0]["source"] == "crawled"
    assert len(store.get_llm_calls()) == 2

    summary = store.get_llm_usage_summary("source")
    assert {row["group_key"] for row in summary} == {"bewt", "crawled"}
    store.close()


def test_update_classification_scoped_by_source(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result(source="bewt"))
    store.save_result(_result(source="crawled"))

    assert store.update_classification(
        "bludit", "CrawledBluditTest0", "A", "gpt-4o",
        "correct", "looks right", source="crawled",
    )
    rows = {row["source"]: row for row in store.get_all_results()}
    assert rows["crawled"]["manual_error_category"] == "correct"
    assert rows["bewt"]["manual_error_category"] is None
    store.close()


def test_load_experiment_results_roundtrips_source(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result(source="crawled"))
    results = store.load_experiment_results()
    assert results[0].source == "crawled"
    store.close()
