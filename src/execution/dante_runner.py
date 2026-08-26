from __future__ import annotations

"""Compile and run crawler-generated (DANTE) tests with the injected assertion.

The DANTE-generated test project is a plain Maven project whose test classes
live in ``src/main/java/tests`` and are executed directly with JUnitCore (see
its ``compile-and-run-fired.sh``). The project targets the 2020 toolchain:
JDK 8, Maven 3.6, Selenium 3.141 + WebDriverManager (ChromeDriver <= 114), so
runs use the pinned toolchain from docs/dante-setup.md, overridable via the
DANTE_JAVA_HOME / DANTE_MAVEN_HOME / DANTE_CHROME_BINARY environment variables.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..models import TestRecord
from .java_injector import inject_assertion
from .test_runner import CompileResult, TestResult

_HOME = Path.home()

DEFAULT_JAVA_HOME = _HOME / "dante-tools" / "zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64" / "Contents" / "Home"
DEFAULT_MAVEN_HOME = _HOME / "dante-tools" / "apache-maven-3.6.0"
DEFAULT_CHROME_BINARY = _HOME / "dante-tools" / "chrome-mac-arm64" / "cft.app" / "Contents" / "MacOS" / "chrome"

# Directories in the generated project that are large and irrelevant to runs.
_SKIP_DIRS = {"coverage-reports", "all-coverage-reports", "target", ".idea"}


def _toolchain_env() -> dict[str, str]:
    java_home = os.environ.get("DANTE_JAVA_HOME", str(DEFAULT_JAVA_HOME))
    maven_home = os.environ.get("DANTE_MAVEN_HOME", str(DEFAULT_MAVEN_HOME))
    chrome_binary = os.environ.get("DANTE_CHROME_BINARY", str(DEFAULT_CHROME_BINARY))

    env = os.environ.copy()
    env["JAVA_HOME"] = java_home
    env["PATH"] = f"{java_home}/bin:{maven_home}/bin:" + env.get("PATH", "")
    env["JAVA_TOOL_OPTIONS"] = f"-Dchrome.binary={chrome_binary}"
    return env


def run_oracle_suite(config, app: str, timeout: int = 3600) -> list[TestResult]:
    """Compile and run the Crawljax oracle suite (GeneratedTests, TestNG).

    This is the baseline in the LLM-vs-Crawljax-oracles comparison: each test
    method replays one crawl path and asserts invariants + DOM/screenshot
    state-equivalence after every event. Results are parsed from TestNG's
    ``test-output/testng-results.xml``.
    """
    import xml.etree.ElementTree as ET

    crawl_dir = config.get_dante_crawl_dir(app)
    libs = config.dante_repo_path / "crawljax" / "libs"
    plugin_classes = (config.dante_repo_path / "crawljax" / "plugins"
                      / "testcasegenerator-plugin" / "target" / "classes")
    generated_src = crawl_dir / "src" / "test" / "java"
    generated_test = generated_src / "generated" / "GeneratedTests.java"

    if not generated_test.exists():
        return []

    env = _toolchain_env()
    classpath = f"{libs}/*:{plugin_classes}:{generated_src}"

    compile_res = subprocess.run(
        ["javac", "-cp", classpath, str(generated_test)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    if compile_res.returncode != 0:
        return [TestResult(
            test_class="generated.GeneratedTests", test_method="compile",
            passed=False, failed=True, errored=True, skipped=False,
            error_message=compile_res.stderr[:1000],
        )]

    subprocess.run(
        ["java", "-cp", classpath, "org.testng.TestNG", "testng.xml"],
        cwd=str(crawl_dir), capture_output=True, text=True,
        timeout=timeout, env=env,
    )

    results_xml = crawl_dir / "test-output" / "testng-results.xml"
    if not results_xml.exists():
        return [TestResult(
            test_class="generated.GeneratedTests", test_method="suite",
            passed=False, failed=True, errored=True, skipped=False,
            error_message="testng-results.xml not produced",
        )]

    results: list[TestResult] = []
    root = ET.parse(results_xml).getroot()
    for method in root.iter("test-method"):
        if method.get("is-config") == "true":
            continue
        name = method.get("name", "")
        status = method.get("status", "")
        message = ""
        exception = method.find(".//message")
        if exception is not None and exception.text:
            message = exception.text.strip()[:1000]
        results.append(TestResult(
            test_class="generated.GeneratedTests",
            test_method=name,
            passed=status == "PASS",
            failed=status == "FAIL",
            errored=False,
            skipped=status == "SKIP",
            error_message=message,
        ))
    return results


def reset_crawled_app(config, app: str, timeout: int = 300) -> bool:
    """Reset the app to a fresh installed state via DANTE's docker runner.

    Crawled replay tests mutate application state (they re-fire the crawl's
    actions), so each executed test starts from a clean container.
    """
    docker_script = config.dante_repo_path / "dante" / "docker" / app / "run-docker.sh"
    if not docker_script.exists():
        return False

    subprocess.run(
        f"docker ps -aq --filter name={app} | xargs -r docker rm -f",
        shell=True, capture_output=True, text=True, timeout=60,
    )
    result = subprocess.run(
        ["./run-docker.sh", "-p", "yes", "-n", f"{app}-pipeline"],
        cwd=str(docker_script.parent),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode == 0


def prepare_crawled_project_copy(
    config,
    app: str,
    record: TestRecord,
    generated_assertion: str,
    variant_source: str,
    treatment: str,
    model: str,
) -> Path:
    """Copy the DANTE test project and inject one assertion-completed class.

    The standalone class produced by ingestion is written into
    ``src/main/java/tests`` next to the generated suite, so it compiles against
    the project's own utils (DriverProvider, Properties).
    """
    project_path = config.get_dante_testsuite_project_path(app)
    work_dir = config.output_dir / "runs-crawled" / app / model / treatment / record.class_name

    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(
        project_path, work_dir,
        ignore=shutil.ignore_patterns(*_SKIP_DIRS),
    )

    test_file = work_dir / "src" / "main" / "java" / "tests" / f"{record.class_name}.java"
    inject_assertion(record, generated_assertion, variant_source, test_file)
    return work_dir


def compile_crawled_project(work_dir: Path, timeout: int = 300) -> CompileResult:
    """``mvn clean compile`` with the pinned JDK 8 / Maven 3.6 toolchain."""
    try:
        result = subprocess.run(
            ["mvn", "clean", "compile", "-q"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_toolchain_env(),
        )
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, errors=["compile timeout"])

    if result.returncode == 0:
        return CompileResult(success=True, errors=[])

    errors = [
        line.strip()
        for line in (result.stdout + "\n" + result.stderr).split("\n")
        if "[ERROR]" in line or "error:" in line
    ]
    return CompileResult(success=False, errors=errors or ["mvn compile failed"])


def _classpath(work_dir: Path) -> str | None:
    """Resolve the project's dependency classpath via Maven (cached per copy)."""
    cp_file = work_dir / ".classpath.txt"
    if cp_file.exists():
        return cp_file.read_text().strip()
    result = subprocess.run(
        ["mvn", "dependency:build-classpath", "-Dmdep.outputFile=.classpath.txt", "-q", "-o"],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=300,
        env=_toolchain_env(),
    )
    if result.returncode != 0 or not cp_file.exists():
        # retry online (first run may need to fetch plugin metadata)
        result = subprocess.run(
            ["mvn", "dependency:build-classpath", "-Dmdep.outputFile=.classpath.txt", "-q"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=600,
            env=_toolchain_env(),
        )
    if cp_file.exists():
        return cp_file.read_text().strip()
    return None


def run_crawled_test(work_dir: Path, class_name: str, timeout: int = 300) -> TestResult:
    """Run one injected test class with JUnitCore against the live app."""
    classpath = _classpath(work_dir)
    if classpath is None:
        return TestResult(
            test_class=class_name, test_method="unknown",
            passed=False, failed=True, errored=True, skipped=False,
            error_message="could not resolve project classpath",
        )

    try:
        result = subprocess.run(
            ["java", "-cp", f"{classpath}:./target/classes",
             "org.junit.runner.JUnitCore", f"tests.{class_name}"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_toolchain_env(),
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            test_class=class_name, test_method="unknown",
            passed=False, failed=True, errored=True, skipped=False,
            error_message="test run timeout",
        )

    output = result.stdout + "\n" + result.stderr
    passed = bool(re.search(r"^OK \(\d+ test", output, re.MULTILINE))

    error_message = ""
    if not passed:
        failure_match = re.search(
            r"^\d+\) .*\n((?:.+\n)*?)(?:\n|\tat )", output, re.MULTILINE
        )
        if failure_match:
            error_message = failure_match.group(0).strip()[:1000]
        else:
            error_message = output.strip()[-1000:]

    return TestResult(
        test_class=class_name,
        test_method="unknown",
        passed=passed,
        failed=not passed,
        errored=False,
        skipped=False,
        error_message=error_message,
    )
