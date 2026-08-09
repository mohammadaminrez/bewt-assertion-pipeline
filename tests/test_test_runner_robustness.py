from __future__ import annotations

import subprocess

import pytest

from src.execution import test_runner

REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="base.TestSuite" tests="1">
  <testcase name="addCategory" classname="base.AddCategoryTest" time="1.0"/>
</testsuite>
"""


def _write_report(project, body=REPORT, name="TEST-base.TestSuite.xml"):
    reports = project / "target" / "surefire-reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / name).write_text(body)


def test_compile_timeout_is_reported_not_raised(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="mvn", timeout=300)
    monkeypatch.setattr(test_runner.subprocess, "run", _boom)

    result = test_runner.compile_project(tmp_path)

    assert result.success is False
    assert "timed out" in result.errors[0]


def test_missing_maven_is_reported_not_raised(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("mvn")
    monkeypatch.setattr(test_runner.subprocess, "run", _boom)

    assert test_runner.compile_project(tmp_path).success is False
    assert test_runner.run_single_test(tmp_path, "AddCategoryTest").passed is False


def test_suite_timeout_does_not_crash_the_run(tmp_path, monkeypatch):
    """A timeout must yield a failed result so the experiment loop continues."""
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="mvn", timeout=3600)
    monkeypatch.setattr(test_runner.subprocess, "run", _boom)

    result = test_runner.run_suite_and_get_result(tmp_path, "AddCategoryTest")

    assert result.passed is False
    assert "timed out" in result.error_message


def test_stale_reports_are_cleared_before_running(tmp_path, monkeypatch):
    """A leftover report from an earlier run must never be read as this run's."""
    _write_report(tmp_path)

    def _fake_run(*a, **k):
        # Maven fails without writing any report.
        return subprocess.CompletedProcess(args=["mvn"], returncode=1, stdout="", stderr="")
    monkeypatch.setattr(test_runner.subprocess, "run", _fake_run)

    result = test_runner.run_suite_and_get_result(tmp_path, "AddCategoryTest")

    assert result.passed is False
    assert "not found in surefire reports" in result.error_message


def test_suite_result_is_found_for_the_target_class(tmp_path, monkeypatch):
    def _fake_run(*a, **k):
        _write_report(tmp_path)
        return subprocess.CompletedProcess(args=["mvn"], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(test_runner.subprocess, "run", _fake_run)

    result = test_runner.run_suite_and_get_result(tmp_path, "AddCategoryTest")

    assert result.passed is True
    assert result.test_class == "base.AddCategoryTest"
