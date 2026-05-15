from __future__ import annotations

import pandas as pd
from click.testing import CliRunner

from src.cli import main
from src.data.store import ResultStore
from src.llm.types import LLMCall


def _write_config(tmp_path):
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
    return config_dir, output_dir


def test_llm_usage_prints_grouped_summary_and_exports_csv(tmp_path):
    config_dir, output_dir = _write_config(tmp_path)
    store = ResultStore(output_dir / "results.db")
    store.save_llm_call(LLMCall(
        call_type="generation",
        app="mantisbt",
        class_name="A",
        method_name="testA",
        treatment="D",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response="raw",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cost_usd=0.001,
        latency_ms=100,
    ))
    store.close()

    output = tmp_path / "usage.csv"
    result = CliRunner().invoke(main, [
        "--config-dir", str(config_dir),
        "llm-usage",
        "--by", "treatment",
        "--output", str(output),
    ])

    assert result.exit_code == 0
    assert "group" in result.output
    assert "D" in result.output
    assert "0.001" in result.output
    df = pd.read_csv(output)
    assert len(df) == 1
    assert df.loc[0, "group"] == "D"
    assert df.loc[0, "input"] == 100
