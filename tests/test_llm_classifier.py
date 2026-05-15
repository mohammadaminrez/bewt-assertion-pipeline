from __future__ import annotations

from src.data.store import ResultStore
from src.evaluation.llm_classifier import pre_classify_results
from src.llm.types import LLMResponse
from src.models import ExperimentResult, TestRecord


class FakeClassifierClient:
    def generate(self, system: str, user: str) -> LLMResponse:
        return LLMResponse(
            text='{"classification": "correct", "reason": "same check"}',
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            cost_usd=0.0012,
            latency_ms=345,
        )


def test_pre_classify_results_logs_pre_classification_call(tmp_path):
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
        model="generation-model",
        prompt="generation prompt",
        raw_response="generation response",
        generated_assertion='assertEquals("x", page.value());',
    )

    classifications = pre_classify_results(
        [result],
        FakeClassifierClient(),
        store=store,
        classifier_model="gpt-4o-mini",
    )
    calls = store.get_llm_calls(call_type="pre_classification")

    assert classifications == {"mantisbt|AddNewProject|D|generation-model": "correct"}
    assert len(calls) == 1
    assert calls[0]["call_type"] == "pre_classification"
    assert calls[0]["app"] == "mantisbt"
    assert calls[0]["class_name"] == "AddNewProject"
    assert calls[0]["method_name"] == "addNewProject"
    assert calls[0]["treatment"] == "D"
    assert calls[0]["model"] == "gpt-4o-mini"
    assert calls[0]["provider"] == "openai"
    assert "Gold standard assertion" in calls[0]["user_prompt"]
    assert calls[0]["raw_response"] == '{"classification": "correct", "reason": "same check"}'
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["output_tokens"] == 20
    assert calls[0]["total_tokens"] == 120
    assert calls[0]["cost_usd"] == 0.0012
    assert calls[0]["latency_ms"] == 345
    store.close()
