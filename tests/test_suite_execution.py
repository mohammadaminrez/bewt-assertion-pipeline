from __future__ import annotations

from pathlib import Path

import pytest

from src import runner
from src.execution.java_injector import classes_with_compile_errors
from src.execution.test_runner import CompileResult, TestResult
from src.models import ExecutionOutcome, ExperimentResult, TestRecord

SOURCE = """package base;
import static org.junit.Assert.assertEquals;
import org.junit.Test;
public class {name} extends BaseTest {{
    @Test public void t() {{
        // TODO: Insert the missing assertion here
    }}
}}
"""


def _record(name: str, tmp_path: Path) -> TestRecord:
    path = tmp_path / "src" / "test" / "java" / "base" / f"{name}.java"
    path.parent.mkdir(parents=True, exist_ok=True)
    source = SOURCE.format(name=name)
    path.write_text(source)
    return TestRecord(
        app="mantisbt", variant="baseline", version="2.25.4",
        file_path=str(path), class_name=name, method_name="t",
        full_source=source, stripped_source=source,
    )


def _result(record: TestRecord) -> ExperimentResult:
    return ExperimentResult(
        test_record=record, treatment="A", model="gpt-4o",
        prompt="p", raw_response="r", generated_assertion="assertTrue(x);",
    )


class _Config:
    def __init__(self, project, out):
        self._project, self.output_dir = project, out

    def get_app_project_path(self, app):
        return self._project


@pytest.fixture
def suite(tmp_path):
    project = tmp_path / "project"
    records = [_record(n, project) for n in ("AddProjectTest", "AddCategoryTest")]
    results = {r.class_name: _result(r) for r in records}
    config = _Config(project, tmp_path / "out")
    return config, records, results


def test_failure_and_error_are_not_the_same_outcome():
    """A JUnit failure judges the assertion; an error means it never ran."""
    failed = TestResult("base.T", "t", passed=False, failed=True, errored=False,
                        skipped=False, error_type="org.junit.ComparisonFailure")
    errored = TestResult("base.T", "t", passed=False, failed=False, errored=True,
                         skipped=False, error_type="NoSuchElementException")
    passed = TestResult("base.T", "t", passed=True, failed=False, errored=False, skipped=False)

    assert runner._outcome_from_test_result(failed)[0] is ExecutionOutcome.FAILED
    assert runner._outcome_from_test_result(errored)[0] is ExecutionOutcome.ERRORED
    assert runner._outcome_from_test_result(passed)[0] is ExecutionOutcome.PASSED


def test_functional_buckets_match_the_report_categories():
    assert ExecutionOutcome.PASSED.functional_bucket == "passed"
    assert ExecutionOutcome.FAILED.functional_bucket == "failed"
    # A test that errored never reached a verdict, so it is not a "Failed".
    assert ExecutionOutcome.ERRORED.functional_bucket == "not_working"
    assert ExecutionOutcome.NOT_COMPILED.functional_bucket == "not_working"


def test_compile_errors_are_attributed_to_the_offending_class(tmp_path):
    records = [_record(n, tmp_path) for n in ("AddProjectTest", "AddCategoryTest")]
    errors = [
        "[ERROR] /x/src/test/java/base/AddCategoryTest.java:[23,22] cannot find symbol",
    ]
    assert classes_with_compile_errors(errors, records) == {"AddCategoryTest"}


def test_suite_runs_once_and_maps_results_back(suite, monkeypatch):
    config, records, results = suite
    calls = {"compile": 0, "suite": 0}

    monkeypatch.setattr(runner, "compile_project",
                        lambda wd: calls.__setitem__("compile", calls["compile"] + 1) or CompileResult(True, []))

    def _fake_suite(work_dir, *a, **k):
        calls["suite"] += 1
        return [
            TestResult("base.AddProjectTest", "t", passed=True, failed=False,
                       errored=False, skipped=False),
            TestResult("base.AddCategoryTest", "t", passed=False, failed=True,
                       errored=False, skipped=False, error_type="org.junit.ComparisonFailure"),
        ]
    monkeypatch.setattr(runner, "run_test_suite", _fake_suite)

    runner.execute_treatment_suite(
        config, "mantisbt", records,
        {r.class_name: "assertTrue(x);" for r in records},
        {r.class_name: SOURCE.format(name=r.class_name) for r in records},
        "A", "gpt-4o", results,
    )

    assert calls["suite"] == 1, "the suite must run once per treatment, not per test"
    assert results["AddProjectTest"].outcome is ExecutionOutcome.PASSED
    assert results["AddCategoryTest"].outcome is ExecutionOutcome.FAILED
    assert results["AddCategoryTest"].error_type == "org.junit.ComparisonFailure"


def test_one_bad_assertion_does_not_sink_the_whole_treatment(suite, monkeypatch):
    """The offender is reverted to gold; everyone else still gets a real result."""
    config, records, results = suite
    state = {"attempt": 0}

    def _fake_compile(work_dir):
        state["attempt"] += 1
        if state["attempt"] == 1:
            return CompileResult(False, [
                "[ERROR] /x/src/test/java/base/AddCategoryTest.java:[9,1] cannot find symbol",
            ])
        return CompileResult(True, [])
    monkeypatch.setattr(runner, "compile_project", _fake_compile)
    monkeypatch.setattr(runner, "run_test_suite", lambda wd, *a, **k: [
        TestResult("base.AddProjectTest", "t", passed=True, failed=False,
                   errored=False, skipped=False),
    ])

    runner.execute_treatment_suite(
        config, "mantisbt", records,
        {r.class_name: "assertTrue(x);" for r in records},
        {r.class_name: SOURCE.format(name=r.class_name) for r in records},
        "A", "gpt-4o", results,
    )

    assert results["AddCategoryTest"].outcome is ExecutionOutcome.NOT_COMPILED
    assert results["AddProjectTest"].outcome is ExecutionOutcome.PASSED


def test_unattributable_build_failure_fails_everyone_rather_than_guessing(suite, monkeypatch):
    config, records, results = suite
    monkeypatch.setattr(runner, "compile_project",
                        lambda wd: CompileResult(False, ["[ERROR] /x/pom.xml broken"]))
    monkeypatch.setattr(runner, "run_test_suite",
                        lambda *a, **k: pytest.fail("suite must not run when the build failed"))

    runner.execute_treatment_suite(
        config, "mantisbt", records,
        {r.class_name: "assertTrue(x);" for r in records},
        {r.class_name: SOURCE.format(name=r.class_name) for r in records},
        "A", "gpt-4o", results,
    )

    assert all(r.outcome is ExecutionOutcome.NOT_COMPILED for r in results.values())


def test_missing_surefire_entry_is_errored_not_passed(suite, monkeypatch):
    config, records, results = suite
    monkeypatch.setattr(runner, "compile_project", lambda wd: CompileResult(True, []))
    monkeypatch.setattr(runner, "run_test_suite", lambda wd, *a, **k: [])

    runner.execute_treatment_suite(
        config, "mantisbt", records,
        {r.class_name: "assertTrue(x);" for r in records},
        {r.class_name: SOURCE.format(name=r.class_name) for r in records},
        "A", "gpt-4o", results,
    )

    assert all(r.outcome is ExecutionOutcome.ERRORED for r in results.values())
    assert all(not r.passes for r in results.values())
