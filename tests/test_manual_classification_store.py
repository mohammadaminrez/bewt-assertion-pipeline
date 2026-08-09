from __future__ import annotations

from src.data.store import ResultStore
from src.models import ErrorCategory, ExperimentResult, TestRecord


def _result(mode: str = "cumulative", **overrides) -> ExperimentResult:
    kwargs = dict(
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
        mode=mode,
        prompt="prompt",
        raw_response="raw",
        generated_assertion='assertEquals("x", page.value());',
    )
    kwargs.update(overrides)
    return ExperimentResult(**kwargs)


def test_update_classification_stores_reviewed_label_separately(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result(compiles=True, error_category=ErrorCategory.PENDING))

    assert store.update_classification(
        app="mantisbt",
        class_name="AddNewProject",
        treatment="D",
        model="gpt-4o-mini",
        error_category="wrong_assertion",
        notes="checks the wrong field",
    )

    row = store.get_all_results()[0]

    # The mechanical label is untouched; the reviewed label lands beside it.
    assert row["error_category"] == "pending"
    assert row["manual_error_category"] == "wrong_assertion"
    assert row["manual_notes"] == "checks the wrong field"
    store.close()


def test_update_classification_scoped_by_mode(tmp_path):
    """Labelling a cumulative row must not relabel the singular row."""
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result(mode="cumulative"))
    store.save_result(_result(mode="singular"))

    assert store.update_classification(
        app="mantisbt", class_name="AddNewProject", treatment="D",
        model="gpt-4o-mini", error_category="correct", mode="cumulative",
    )

    by_mode = {row["mode"]: row for row in store.get_all_results()}
    assert by_mode["cumulative"]["manual_error_category"] == "correct"
    assert by_mode["singular"]["manual_error_category"] is None
    store.close()


def test_reviewed_label_wins_when_reloaded(tmp_path):
    store = ResultStore(tmp_path / "results.db")
    store.save_result(_result(compiles=True))
    store.update_classification(
        app="mantisbt", class_name="AddNewProject", treatment="D",
        model="gpt-4o-mini", error_category="under_assertive",
    )

    loaded = store.load_experiment_results()[0]

    assert loaded.error_category is ErrorCategory.PENDING
    assert loaded.final_category == "under_assertive"
    store.close()
