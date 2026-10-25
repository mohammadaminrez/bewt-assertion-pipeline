from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.runner import MissingContextError, _check_html_available

TEST_SOURCE = """package tests;

public class {name} extends BaseTest {{
\t@Test
\tpublic void run() {{
\t\tassertEquals("x", page.value());
\t}}
}}
"""


def _config(tmp_path, tests=("AddCategoryTest", "DeletePostTest"), captured=("AddCategoryTest",)):
    test_dir = tmp_path / "project" / "src" / "test" / "java" / "tests"
    test_dir.mkdir(parents=True)
    for name in tests:
        (test_dir / f"{name}.java").write_text(TEST_SOURCE.format(name=name))
    html_dir = tmp_path / "output" / "html_capture" / "bludit"
    html_dir.mkdir(parents=True)
    for name in captured:
        (html_dir / f"{name}.html").write_text("<html></html>")
    return SimpleNamespace(
        apps={"bludit": {"versions": ["3.13.1"], "variant": "baseline"}},
        output_dir=tmp_path / "output",
        get_app_test_path=lambda app: test_dir,
        get_app_project_path=lambda app: tmp_path / "project",
    )


def test_missing_html_fails_before_running_html_treatments(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(MissingContextError, match="bludit/DeletePostTest"):
        _check_html_available(config, ["bludit"], ("A", "B", "C"), None)


def test_missing_html_is_irrelevant_without_html_treatments(tmp_path):
    config = _config(tmp_path)
    _check_html_available(config, ["bludit"], ("A", "B"), None)


def test_complete_html_passes(tmp_path):
    config = _config(tmp_path, captured=("AddCategoryTest", "DeletePostTest"))
    _check_html_available(config, ["bludit"], ("A", "B", "C", "D"), None)
