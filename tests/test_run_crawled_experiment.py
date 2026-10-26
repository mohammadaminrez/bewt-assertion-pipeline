from pathlib import Path

import pytest

import src.runner as runner_module
from src.data.store import ResultStore
from src.llm.types import LLMResponse
from src.runner import run_crawled_experiment

from tests.test_dante_ingest import SUITE_SOURCE


class FakeLLM:
    def __init__(self):
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return LLMResponse(
            text='assertTrue(driver.getPageSource().contains("saved"));',
            provider="fake", model="fake-model",
            input_tokens=10, output_tokens=5, total_tokens=15,
        )


class FakeConfig:
    """Minimal Config stand-in wired to tmp DANTE fixture dirs."""

    def __init__(self, tmp_path: Path):
        self.apps = {"bludit": {"versions": ["3.13.1"], "variant": "baseline"}}
        self.default_model = "fake-model"
        self.output_dir = tmp_path / "output"
        self._suite = tmp_path / "GeneratedTestSuiteFired.java"

    def get_dante_suite_path(self, app):
        return self._suite


@pytest.fixture
def config(tmp_path):
    cfg = FakeConfig(tmp_path)
    cfg._suite.write_text(SUITE_SOURCE)
    # pages captured by the crawled reference run at each test's assertion point
    html_dir = cfg.output_dir / "reference-crawled" / "bludit" / "html"
    html_dir.mkdir(parents=True)
    for name in ("CrawledBluditTest0", "CrawledBluditTest1"):
        (html_dir / f"{name}.html").write_text(f"<html>captured {name}</html>")
    return cfg


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(runner_module, "create_client", lambda config, model: llm)
    return llm


def test_run_crawled_experiment_stores_results_with_crawled_source(config, fake_llm):
    results = run_crawled_experiment(
        config, "bludit", models=["fake-model"], treatments=("A", "B", "C"),
    )

    # 2 tests x 3 treatments
    assert len(results) == 6
    assert all(r.source == "crawled" for r in results)
    assert all(r.generated_assertion for r in results)
    assert all(r.notes.startswith("execution skipped") for r in results)

    store = ResultStore(config.output_dir / "results.db")
    rows = store.get_all_results()
    assert len(rows) == 6
    assert all(row["source"] == "crawled" for row in rows)
    calls = store.get_llm_calls(source="crawled")
    assert len(calls) == 6
    assert all(call["experiment_id"] is not None for call in calls)
    store.close()


def test_crawled_treatments_control_prompt_context(config, fake_llm):
    run_crawled_experiment(
        config, "bludit", models=["fake-model"], treatments=("A", "B", "C"), limit=1,
    )

    prompts = [user for _, user in fake_llm.calls]
    prompt_a, prompt_b, prompt_c = prompts

    assert "// The test" not in prompt_a and "```html" not in prompt_a
    assert "// The test" in prompt_b and "```html" not in prompt_b
    assert "// The test" in prompt_c and "```html" in prompt_c
    # Treatment C carries the page captured at the assertion point (cleaned).
    assert "<html>\ncaptured CrawledBluditTest0\n</html>" in prompt_c


def test_run_crawled_experiment_is_resumable(config, fake_llm):
    first = run_crawled_experiment(config, "bludit", models=["fake-model"],
                                   treatments=("A",))
    assert len(first) == 2
    again = run_crawled_experiment(config, "bludit", models=["fake-model"],
                                   treatments=("A",))
    assert len(again) == 0  # everything already stored → skipped
    assert len(fake_llm.calls) == 2
