from __future__ import annotations

"""Run Maven tests and capture results."""

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestResult:
    test_class: str
    test_method: str
    passed: bool
    failed: bool
    errored: bool
    skipped: bool
    error_message: str = ""
    error_type: str = ""
    time_seconds: float = 0.0


@dataclass
class CompileResult:
    success: bool
    errors: list[str]


def compile_project(project_path: Path) -> CompileResult:
    """Compile the Maven project and return the result."""
    result = subprocess.run(
        ["mvn", "compile", "test-compile", "-q"],
        cwd=str(project_path),
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode == 0:
        return CompileResult(success=True, errors=[])

    # Extract compilation errors
    errors = []
    for line in result.stdout.split('\n') + result.stderr.split('\n'):
        if '[ERROR]' in line:
            errors.append(line.strip())

    return CompileResult(success=False, errors=errors)


def run_test_suite(project_path: Path, timeout: int = 300) -> list[TestResult]:
    """Run the full test suite and return results for each test."""
    result = subprocess.run(
        ["mvn", "test", "-Dtest=TestSuite"],
        cwd=str(project_path),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    # Parse surefire reports
    reports_dir = project_path / "target" / "surefire-reports"
    return _parse_surefire_reports(reports_dir)


def run_single_test(
    project_path: Path,
    test_class: str,
    timeout: int = 120,
) -> TestResult:
    """Run a single test class and return the result."""
    result = subprocess.run(
        ["mvn", "test", f"-Dtest={test_class}"],
        cwd=str(project_path),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    reports_dir = project_path / "target" / "surefire-reports"
    results = _parse_surefire_reports(reports_dir)

    # Find the specific test result
    for r in results:
        if r.test_class == test_class or r.test_class.endswith(f".{test_class}"):
            return r

    # If not found in reports, construct from process result
    return TestResult(
        test_class=test_class,
        test_method="unknown",
        passed=result.returncode == 0,
        failed=result.returncode != 0,
        errored=False,
        skipped=False,
        error_message=result.stderr if result.returncode != 0 else "",
    )


def run_suite_and_get_result(
    project_path: Path,
    target_class: str,
    timeout: int = 300,
) -> TestResult:
    """Run the full test suite and return the result for a specific test class.

    This is needed because tests have dependencies — running a single test
    in isolation may fail due to missing state from previous tests.
    """
    results = run_test_suite(project_path, timeout)

    for r in results:
        if r.test_class == target_class or r.test_class.endswith(f".{target_class}"):
            return r

    return TestResult(
        test_class=target_class,
        test_method="unknown",
        passed=False,
        failed=True,
        errored=True,
        skipped=False,
        error_message="Test result not found in surefire reports",
    )


def _parse_surefire_reports(reports_dir: Path) -> list[TestResult]:
    """Parse Maven Surefire XML reports."""
    results = []

    if not reports_dir.exists():
        return results

    for xml_file in reports_dir.glob("TEST-*.xml"):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()

            for testcase in root.findall('.//testcase'):
                class_name = testcase.get('classname', '')
                method_name = testcase.get('name', '')
                time_s = float(testcase.get('time', '0'))

                failure = testcase.find('failure')
                error = testcase.find('error')
                skipped = testcase.find('skipped')

                results.append(TestResult(
                    test_class=class_name,
                    test_method=method_name,
                    passed=failure is None and error is None and skipped is None,
                    failed=failure is not None,
                    errored=error is not None,
                    skipped=skipped is not None,
                    error_message=(
                        failure.get('message', '') if failure is not None
                        else error.get('message', '') if error is not None
                        else ''
                    ),
                    error_type=(
                        failure.get('type', '') if failure is not None
                        else error.get('type', '') if error is not None
                        else ''
                    ),
                    time_seconds=time_s,
                ))
        except ET.ParseError:
            continue

    return results
