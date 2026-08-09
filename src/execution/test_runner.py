from __future__ import annotations

"""Run Maven tests and capture results."""

import shutil
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


COMPILE_TIMEOUT = 300
SUITE_TIMEOUT = 3600
SINGLE_TEST_TIMEOUT = 600


def _clear_surefire_reports(project_path: Path) -> None:
    """Delete previous Surefire reports so only this run's results are parsed."""
    reports_dir = project_path / "target" / "surefire-reports"
    if reports_dir.exists():
        shutil.rmtree(reports_dir, ignore_errors=True)


def compile_project(project_path: Path, timeout: int = COMPILE_TIMEOUT) -> CompileResult:
    """Compile the Maven project and return the result."""
    try:
        result = subprocess.run(
            ["mvn", "compile", "test-compile", "-q"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, errors=[f"Compilation timed out after {timeout}s"])
    except FileNotFoundError:
        return CompileResult(success=False, errors=["Maven ('mvn') not found on PATH"])

    if result.returncode == 0:
        return CompileResult(success=True, errors=[])

    # Extract compilation errors
    errors = []
    for line in result.stdout.split('\n') + result.stderr.split('\n'):
        if '[ERROR]' in line:
            errors.append(line.strip())

    return CompileResult(success=False, errors=errors)


def _run_maven_tests(project_path: Path, selector: str, timeout: int) -> tuple[list[TestResult], str]:
    """Run `mvn test` for a selector and parse the reports it produces.

    Returns the parsed results plus an error string, which is empty on a normal
    run. Maven failing is not itself an error here — a failing test also exits
    non-zero, and the Surefire reports carry the detail.
    """
    _clear_surefire_reports(project_path)
    try:
        subprocess.run(
            ["mvn", "test", f"-Dtest={selector}"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _parse_surefire_reports(project_path / "target" / "surefire-reports"), (
            f"Test run timed out after {timeout}s"
        )
    except FileNotFoundError:
        return [], "Maven ('mvn') not found on PATH"

    return _parse_surefire_reports(project_path / "target" / "surefire-reports"), ""


def run_test_suite(project_path: Path, timeout: int = SUITE_TIMEOUT) -> list[TestResult]:
    """Run the full test suite and return results for each test."""
    results, _ = _run_maven_tests(project_path, "TestSuite", timeout)
    return results


def run_single_test(
    project_path: Path,
    test_class: str,
    timeout: int = SINGLE_TEST_TIMEOUT,
) -> TestResult:
    """Run a single test class and return the result.

    Note: BEWT tests depend on state created by earlier tests in TestSuite, so
    this is only meaningful for tests with no preconditions. Use
    ``run_suite_and_get_result`` for the general case.
    """
    results, error = _run_maven_tests(project_path, test_class, timeout)

    for r in results:
        if r.test_class == test_class or r.test_class.endswith(f".{test_class}"):
            return r

    return TestResult(
        test_class=test_class,
        test_method="unknown",
        passed=False,
        failed=True,
        errored=True,
        skipped=False,
        error_message=error or "Test result not found in surefire reports",
    )


def run_suite_and_get_result(
    project_path: Path,
    target_class: str,
    timeout: int = SUITE_TIMEOUT,
) -> TestResult:
    """Run the full test suite and return the result for a specific test class.

    This is needed because tests have dependencies — running a single test
    in isolation may fail due to missing state from previous tests.
    """
    results, error = _run_maven_tests(project_path, "TestSuite", timeout)

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
        error_message=error or "Test result not found in surefire reports",
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
