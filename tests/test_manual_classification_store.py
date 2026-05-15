from __future__ import annotations

from src.data.store import ResultStore
from src.models import ErrorCategory, ExperimentResult, TestRecord


def test_update_classification_preserves_automatic_error_category(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    result = ExperimentResult(
        test_record=TestRecord(
            app="mantisbt",
            variant="v1",
            version="1.0",
            file_path="AddNewProject.java",
            class_name="AddNewProject",
            method_name="addNewProject",
        ),
        treatment="D",
        model="gpt-4o-mini",
        prompt="prompt",
        raw_response="raw",
        generated_assertion='assertEquals("x", page.value());',
        error_category=ErrorCategory.UNDER_ASSERTIVE,
    )
    store.save_result(result)

    assert store.update_classification(
        app="mantisbt",
        class_name="AddNewProject",
        treatment="D",
        model="gpt-4o-mini",
        error_category="wrong_assertion",
        notes="manual correction",
    )
    assert store.update_llm_preclassification(
        app="mantisbt",
        class_name="AddNewProject",
        treatment="D",
        model="gpt-4o-mini",
        llm_preclassification="correct",
    )

    row = store.get_all_results()[0]

    assert row["error_category"] == "under_assertive"
    assert row["manual_error_category"] == "wrong_assertion"
    assert row["manual_notes"] == "manual correction"
    assert row["llm_preclassification"] == "correct"
    store.close()
