from __future__ import annotations

import pandas as pd
from click.testing import CliRunner

from src.cli import main
from src.data.store import ResultStore
from src.llm.types import LLMCall
from src.models import ExperimentResult, TestRecord


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


def test_export_treatment_comparison_cli_writes_csv(tmp_path):
    config_dir, output_dir = _write_config(tmp_path)
    store = ResultStore(output_dir / "results.db")
    result = ExperimentResult(
        test_record=TestRecord(
            app="mantisbt",
            variant="v1",
            version="1.0",
            file_path="AddNewProject.java",
            class_name="AddNewProject",
            method_name="addNewProject",
        ),
        treatment="A",
        model="gpt-4o-mini",
        prompt="prompt",
        raw_response="raw",
        generated_assertion='assertEquals("x", page.value());',
    )
    experiment_id = store.save_result(result)
    store.save_llm_call(LLMCall(
        experiment_id=experiment_id,
        call_type="generation",
        app="mantisbt",
        class_name="AddNewProject",
        method_name="addNewProject",
        treatment="A",
        model="gpt-4o-mini",
        provider="openai",
        system_prompt="system",
        user_prompt="user",
        raw_response="raw",
        input_tokens=100,
    ))
    store.close()

    output = tmp_path / "comparison.csv"
    result = CliRunner().invoke(main, [
        "--config-dir", str(config_dir),
        "export-treatment-comparison",
        "--output", str(output),
    ])

    assert result.exit_code == 0
    assert "Exported treatment comparison" in result.output
    df = pd.read_csv(output)
    assert len(df) == 1
    assert df.loc[0, "app"] == "mantisbt"
    assert df.loc[0, "A_input_tokens"] == 100
