from __future__ import annotations

from src.data.store import ResultStore
from src.llm.types import LLMCall
from src.models import ExperimentResult, TestRecord


def test_get_treatment_comparison_records_joins_results_to_generation_calls(tmp_path):
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
        exact_match=True,
        semantic_similarity=0.9,
    )
    experiment_id = store.save_result(result)
    assert store.update_classification(
        app="mantisbt",
        class_name="AddNewProject",
        treatment="D",
        model="gpt-4o-mini",
        error_category="wrong_assertion",
        notes="manual note",
    )
    assert store.update_llm_preclassification(
        app="mantisbt",
        class_name="AddNewProject",
        treatment="D",
        model="gpt-4o-mini",
        llm_preclassification="under_assertive",
    )
    store.save_llm_call(LLMCall(
        experiment_id=experiment_id,
        call_type="generation",
        app="mantisbt",
        class_name="AddNewProject",
        method_name="addNewProject",
        treatment="D",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response="raw",
        input_tokens=300,
        output_tokens=40,
        total_tokens=340,
        cost_usd=0.003,
        latency_ms=300,
    ))

    rows = store.get_treatment_comparison_records()

    assert len(rows) == 1
    assert rows[0]["app"] == "mantisbt"
    assert rows[0]["class_name"] == "AddNewProject"
    assert rows[0]["method_name"] == "addNewProject"
    assert rows[0]["treatment"] == "D"
    assert rows[0]["model"] == "gpt-4o-mini"
    assert rows[0]["input_tokens"] == 300
    assert rows[0]["output_tokens"] == 40
    assert rows[0]["total_tokens"] == 340
    assert rows[0]["cost_usd"] == 0.003
    assert rows[0]["latency_ms"] == 300
    assert rows[0]["exact_match"] == 1
    assert rows[0]["semantic_similarity"] == 0.9
    assert rows[0]["error_category"] == "not_executable"
    assert rows[0]["manual_error_category"] == "wrong_assertion"
    assert rows[0]["llm_preclassification"] == "under_assertive"
    store.close()
