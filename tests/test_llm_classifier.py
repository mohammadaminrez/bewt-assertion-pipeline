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


def test_pre_classify_results_logs_pre_classification_call(tmp_path, monkeypatch):
    import src.evaluation.llm_classifier as llm_classifier

    emitted = []
    monkeypatch.setattr(llm_classifier, "emit_llm_call", emitted.append)
    monkeypatch.setattr(llm_classifier, "flush_observability", lambda: None)
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

    group = {"app": "mantisbt", "class_name": "AddNewProject", "answer_key": "abc123",
             "gold": 'assertEquals("x", page.get());', "answer": result.generated_assertion,
             "occurrences": ["generation-model/D/run1"], "result": result}
    classifications = pre_classify_results(
        [group],
        FakeClassifierClient(),
        store=store,
        classifier_model="gpt-4o-mini",
    )
    calls = store.get_llm_calls(call_type="pre_classification")

    assert classifications == {"mantisbt|AddNewProject|abc123": "correct"}
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
    assert len(emitted) == 1
    assert emitted[0].id == calls[0]["id"]
    assert emitted[0].call_type == "pre_classification"
    store.close()


def test_classifier_reply_parsing_never_reads_incorrect_as_correct():
    from src.evaluation.llm_classifier import REVIEW_CATEGORIES, _parse_classification

    assert _parse_classification('{"classification": "under_assertive", "reason": "x"}', REVIEW_CATEGORIES) == "under_assertive"
    assert _parse_classification("This is incorrect: wrong_assertion", REVIEW_CATEGORIES) == "wrong_assertion"
    assert _parse_classification("incorrect", REVIEW_CATEGORIES) == ""
    assert _parse_classification("correct or over_assertive?", REVIEW_CATEGORIES) == ""
