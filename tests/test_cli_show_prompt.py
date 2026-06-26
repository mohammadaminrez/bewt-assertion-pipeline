from __future__ import annotations

from click.testing import CliRunner

from src.cli import main
from src.data.store import ResultStore
from src.llm.types import LLMCall


def test_show_prompt_prints_logged_call(tmp_path):
    config_dir = tmp_path / "config"
    output_dir = tmp_path / "output"
    bewt_dir = tmp_path / "bewt"
    config_dir.mkdir()
    output_dir.mkdir()
    bewt_dir.mkdir()
    (config_dir / "apps.yaml").write_text(f"""
bewt_repo_path: "{bewt_dir}"
output_dir: "{output_dir}"
apps: {{}}
""")
    (config_dir / "models.yaml").write_text("""
default_model: "gpt-4o-mini"
retry_attempts: 1
cache_responses: false
models: {}
""")
    (config_dir / "metrics.yaml").write_text("""
evaluation:
  error_categories: []
  significance_level: 0.05
""")

    store = ResultStore(output_dir / "results.db")
    store.save_llm_call(LLMCall(
        call_type="generation",
        app="mantisbt",
        class_name="AddNewProject",
        method_name="addNewProject",
        treatment="D",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system prompt",
        user_prompt="user prompt",
        raw_response="raw response",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cost_usd=0.0012,
        latency_ms=345,
    ))
    store.close()

    result = CliRunner().invoke(main, [
        "--config-dir", str(config_dir),
        "show-prompt",
        "--app", "mantisbt",
        "--class", "AddNewProject",
        "--treatment", "D",
        "--model", "gpt-4o-mini",
    ])

    assert result.exit_code == 0
    assert "Type: generation" in result.output
    assert "Treatment: D" in result.output
    assert "Input tokens: 100" in result.output
    assert "Cost USD: 0.0012" in result.output
    assert "--- SYSTEM PROMPT ---" in result.output
    assert "system prompt" in result.output
    assert "--- USER PROMPT ---" in result.output
    assert "user prompt" in result.output
    assert "--- RAW RESPONSE ---" in result.output
    assert "raw response" in result.output


def test_show_prompt_requires_latest_when_multiple_calls_match(tmp_path):
    config_dir = tmp_path / "config"
    output_dir = tmp_path / "output"
    bewt_dir = tmp_path / "bewt"
    config_dir.mkdir()
    output_dir.mkdir()
    bewt_dir.mkdir()
    (config_dir / "apps.yaml").write_text(f"""
bewt_repo_path: "{bewt_dir}"
output_dir: "{output_dir}"
apps: {{}}
""")
    (config_dir / "models.yaml").write_text("""
default_model: "gpt-4o-mini"
retry_attempts: 1
cache_responses: false
models: {}
""")
    (config_dir / "metrics.yaml").write_text("""
evaluation:
  error_categories: []
  significance_level: 0.05
""")

    store = ResultStore(output_dir / "results.db")
    for class_name in ("A", "B"):
        store.save_llm_call(LLMCall(
            call_type="generation",
            app="mantisbt",
            class_name=class_name,
            method_name="test",
            treatment="D",
            model="gpt-4o-mini",
            provider="openai",
            system_prompt="system",
            user_prompt="user",
            raw_response="raw",
        ))
    store.close()

    result = CliRunner().invoke(main, [
        "--config-dir", str(config_dir),
        "show-prompt",
        "--app", "mantisbt",
    ])

    assert result.exit_code != 0
    assert "Found 2 matching calls" in result.output
