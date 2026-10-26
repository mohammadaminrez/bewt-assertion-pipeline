from __future__ import annotations

"""DANTE toolchain, app reset and the Crawljax oracle suite.

The DANTE-generated projects target the 2020 toolchain: JDK 8, Maven 3.6,
Selenium 3.141 + WebDriverManager (ChromeDriver <= 114), so they run with the
pinned toolchain from docs/dante-setup.md, overridable via the
DANTE_JAVA_HOME / DANTE_MAVEN_HOME / DANTE_CHROME_BINARY environment variables.
Crawled answers themselves are executed by execution/reference.py.
"""

import os
import shutil
import subprocess
from pathlib import Path

from .test_runner import TestResult

_HOME = Path.home()

DEFAULT_JAVA_HOME = _HOME / "dante-tools" / "zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64" / "Contents" / "Home"
DEFAULT_MAVEN_HOME = _HOME / "dante-tools" / "apache-maven-3.6.0"
DEFAULT_CHROME_BINARY = _HOME / "dante-tools" / "chrome-mac-arm64" / "cft.app" / "Contents" / "MacOS" / "chrome"
# The oracle suite's visual diff loads OpenCV 3.4.3 natives, which exist only
# for x86_64 on macOS; on Apple Silicon the suite runs on an x64 JDK (Rosetta).
DEFAULT_ORACLE_JAVA_HOME = (_HOME / "dante-tools" / "zulu8.96.0.205-ca-jdk8.0.504-macosx_x64"
                            / "Contents" / "Home")



def _toolchain_env(java_home: str | None = None) -> dict[str, str]:
    java_home = java_home or os.environ.get("DANTE_JAVA_HOME", str(DEFAULT_JAVA_HOME))
    maven_home = os.environ.get("DANTE_MAVEN_HOME", str(DEFAULT_MAVEN_HOME))
    chrome_binary = os.environ.get("DANTE_CHROME_BINARY", str(DEFAULT_CHROME_BINARY))

    env = os.environ.copy()
    env["JAVA_HOME"] = java_home
    env["PATH"] = f"{java_home}/bin:{maven_home}/bin:" + env.get("PATH", "")
    # Same reset as the crawl: clear cookies before each path (Crawler.reset,
    # TestSuiteHelper.goToInitialUrl and the generated suites honour it).
    env["JAVA_TOOL_OPTIONS"] = f"-Dchrome.binary={chrome_binary} -Dcrawljax.reset.clearCookies=true"
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
    crawljax = config.dante_repo_path / "crawljax"
    # crawljax/libs holds jars vendored in 2020 (including an old crawljax-core);
    # the freshly built core and plugin come first so the clone's fixes apply.
    core_jar = crawljax / "core" / "target" / "crawljax-core-4.2-SNAPSHOT.jar"
    plugin_classes = crawljax / "plugins" / "testcasegenerator-plugin" / "target" / "classes"
    libs = crawljax / "libs"
    generated_src = crawl_dir / "src" / "test" / "java"
    generated_test = generated_src / "generated" / "GeneratedTests.java"

    if not generated_test.exists():
        return []

    env = _toolchain_env(os.environ.get("DANTE_ORACLE_JAVA_HOME", str(DEFAULT_ORACLE_JAVA_HOME)))
    classpath = f"{core_jar}:{plugin_classes}:{generated_src}:{libs}/*"
    shutil.rmtree(crawl_dir / "test-output", ignore_errors=True)

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
