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
    monkeypatch.setattr(runner_module, "create_client", lambda config, model, run=1: llm)
    return llm


def test_run_crawled_experiment_stores_results_with_crawled_source(config, fake_llm):
    results = run_crawled_experiment(config, "bludit", models=["fake-model"])

    # 2 tests, one condition each
    assert len(results) == 2
    assert all(r.source == "crawled" for r in results)
    assert all(r.generated_assertion for r in results)
    assert all(r.notes.startswith("execution skipped") for r in results)

    store = ResultStore(config.output_dir / "results.db")
    rows = store.get_all_results()
    assert len(rows) == 2
    assert all(row["source"] == "crawled" for row in rows)
    calls = store.get_llm_calls(source="crawled")
    assert len(calls) == 2
    assert all(call["experiment_id"] is not None for call in calls)
    store.close()


def test_crawled_prompt_has_trace_and_captured_page(config, fake_llm):
    run_crawled_experiment(config, "bludit", models=["fake-model"], limit=1)

    [(_, prompt)] = fake_llm.calls
    assert "// The test" in prompt
    # the page captured at the assertion point, unchanged
    assert "```html\n<html>captured CrawledBluditTest0</html>\n```" in prompt


def test_run_crawled_experiment_needs_captured_pages(config, fake_llm):
    (config.output_dir / "reference-crawled" / "bludit" / "html" / "CrawledBluditTest1.html").unlink()
    with pytest.raises(runner_module.MissingContextError, match="CrawledBluditTest1"):
        run_crawled_experiment(config, "bludit", models=["fake-model"])
    assert fake_llm.calls == []


def test_run_crawled_experiment_is_resumable(config, fake_llm):
    first = run_crawled_experiment(config, "bludit", models=["fake-model"])
    assert len(first) == 2
    again = run_crawled_experiment(config, "bludit", models=["fake-model"])
    assert len(again) == 0  # everything already stored → skipped
    assert len(fake_llm.calls) == 2
