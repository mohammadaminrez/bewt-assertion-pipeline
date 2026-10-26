from __future__ import annotations

"""Reference run and isolated execution of generated assertions.

BEWT tests depend on each other (a test deletes the post an earlier test
created) and must run in TestSuite order on a fresh app. The reference run
executes the gold suite once, in that order, on a freshly created container
and records, after every test:

- ``snapshots/NN_<Class>.tar`` — the app's whole state directory
  (``00`` = after the Installer);
- ``html/<Class>.html`` — the page source right after the test's first gold
  assertion group passed (the treatment C/D context).

A generated assertion is then executed in isolation: the app is restored to
the snapshot taken just before its test, and only that test runs. Its verdict
therefore depends on the assertion alone, never on what ran before it.
"""

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from ..config import Config
from ..parsing.java_parser import parse_all_tests
from .java_injector import ensure_assert_imports, fix_java_version, localize_base_url
from .test_runner import CompileResult, TestResult

Log = Callable[[str], None]


class ReferenceRunError(RuntimeError):
    """The gold suite did not run cleanly, so no trustworthy reference exists."""


def reference_dir(config: Config, app: str) -> Path:
    return config.output_dir / "reference" / app


def _docker(config: Config, app: str) -> dict:
    docker = config.apps[app].get("docker")
    if not docker:
        raise ReferenceRunError(f"No 'docker' settings for {app} in config/apps.yaml")
    return docker


# --- app container and state -------------------------------------------------

def _wait_for_http(url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=5)
            return
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return
        except Exception:
            pass
        time.sleep(1)
    raise ReferenceRunError(f"App did not answer at {url} within {timeout:.0f}s")


def reset_app(config: Config, app: str) -> None:
    """Destroy the app container and create a fresh, uninstalled one."""
    docker = _docker(config, app)
    subprocess.run(["docker", "rm", "-f", docker["container"]], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", docker["container"], "-p", docker["ports"], docker["image"]],
        capture_output=True, text=True, check=True,
    )
    _wait_for_http(config.apps[app]["base_url"])


def ensure_app_running(config: Config, app: str) -> None:
    """Create the container if it is missing or stopped. Its state is always
    restored from a snapshot before use, so a fresh container is fine."""
    docker = _docker(config, app)
    state = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", docker["container"]],
        capture_output=True, text=True,
    )
    if state.stdout.strip() != "true":
        reset_app(config, app)


def snapshot_state(config: Config, app: str, dest: Path) -> None:
    docker = _docker(config, app)
    state = Path(docker["state_dir"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        subprocess.run(
            ["docker", "exec", docker["container"], "tar", "-C", str(state.parent), "-cf", "-", state.name],
            stdout=out, check=True,
        )


def restore_state(config: Config, app: str, snapshot: Path) -> None:
    docker = _docker(config, app)
    state = Path(docker["state_dir"])
    subprocess.run(["docker", "exec", docker["container"], "rm", "-rf", str(state)], check=True)
    with snapshot.open("rb") as src:
        subprocess.run(
            ["docker", "exec", "-i", docker["container"], "tar", "-C", str(state.parent), "-xf", "-"],
            stdin=src, check=True,
        )


# --- Java project ------------------------------------------------------------

def suite_order(test_dir: Path) -> list[str]:
    """Test classes in TestSuite.java's @SuiteClasses order (Installer first)."""
    source = (test_dir / "TestSuite.java").read_text()
    block = re.search(r"@SuiteClasses\s*\(\s*\{(.*?)\}\s*\)", source, re.DOTALL).group(1)
    block = re.sub(r"//.*", "", block)
    return re.findall(r"(\w+)\.class", block)


def _prepare_project(config: Config, app: str, dest: Path, log: Log) -> str:
    """Copy the BEWT project, adapt it to this machine, compile it; returns the
    classpath (test classes, main classes, dependencies)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(config.get_app_project_path(app), dest, ignore=shutil.ignore_patterns("target"))
    for changed in localize_base_url(dest, config.apps[app]["base_url"]):
        log(f"  base URL localized in {changed.name}")
    fix_java_version(dest)

    for cmd in (["mvn", "-q", "test-compile"],
                ["mvn", "-q", "dependency:build-classpath", "-Dmdep.outputFile=classpath.txt"]):
        result = subprocess.run(cmd, cwd=str(dest), capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise ReferenceRunError(f"{' '.join(cmd)} failed:\n{(result.stdout + result.stderr)[-2000:]}")
    deps = (dest / "classpath.txt").read_text().strip()
    return f"{dest / 'target' / 'test-classes'}:{dest / 'target' / 'classes'}:{deps}"


def compile_java(sources: list[Path], out_dir: Path, classpath: str) -> CompileResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["javac", "-encoding", "UTF-8", "-nowarn", "-d", str(out_dir), "-cp", classpath,
         *[str(s) for s in sources]],
        capture_output=True, text=True, timeout=300,
    )
    errors = [line for line in result.stderr.splitlines() if "error:" in line]
    return CompileResult(success=result.returncode == 0, errors=errors or result.stderr.splitlines()[:5])


def run_junit(classpath: str, fqcn: str, timeout: int = 300) -> TestResult:
    """Run one test class with JUnitCore."""
    class_name = fqcn.rsplit(".", 1)[-1]
    started = time.time()
    try:
        result = subprocess.run(
            ["java", "-cp", classpath, "org.junit.runner.JUnitCore", fqcn],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TestResult(class_name, "unknown", passed=False, failed=True, errored=True,
                          skipped=False, error_message=f"timeout after {timeout}s")
    output = result.stdout + "\n" + result.stderr
    passed = result.returncode == 0 and bool(re.search(r"^OK \(\d+ test", output, re.MULTILINE))
    message, error_type = "", ""
    if not passed:
        failure = re.search(r"^\d+\) \S+\n(.+)", output, re.MULTILINE)
        message = failure.group(1).strip()[:1000] if failure else output.strip()[-1000:]
        error_type = message.split(":", 1)[0] if failure else ""
    return TestResult(class_name, "unknown", passed=passed, failed=not passed, errored=False,
                      skipped=False, error_message=message, error_type=error_type,
                      time_seconds=round(time.time() - started, 2))


def _capture_statement(html_path: Path) -> str:
    return (
        "try { java.nio.file.Files.write(java.nio.file.Paths.get(\"" + str(html_path) + "\"), "
        "driver.getPageSource().getBytes(java.nio.charset.StandardCharsets.UTF_8)); } "
        "catch (java.io.IOException captureError) { throw new RuntimeException(captureError); }"
    )


# --- reference run -----------------------------------------------------------

def build_reference(config: Config, app: str, log: Log = print) -> dict:
    """Run the gold suite once on a fresh app; save per-test snapshots and HTML."""
    root = reference_dir(config, app)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    project = root / "project"

    log("  Preparing and compiling the test project...")
    classpath = _prepare_project(config, app, project, log)
    package = config.get_app_test_path(app).name
    test_dir = project / "src" / "test" / "java" / package
    order = suite_order(test_dir)
    records = {r.class_name: r for r in parse_all_tests(test_dir, app, "", "")}

    # Instrumented copies capture the page right after the first gold
    # assertion group has passed, so the asserted content is surely present.
    html_dir = root / "html"
    html_dir.mkdir()
    instr_src = root / "instrumented-src" / package
    instr_src.mkdir(parents=True)
    for name, record in records.items():
        lines = (test_dir / f"{name}.java").read_text().split("\n")
        first_block_end = max(a.end_line for a in record.assertions if a.block == 0)
        indent_line = lines[first_block_end - 1]
        indent = indent_line[:len(indent_line) - len(indent_line.lstrip())]
        lines.insert(first_block_end, indent + _capture_statement(html_dir / f"{name}.html"))
        (instr_src / f"{name}.java").write_text("\n".join(lines))
    instr_classes = root / "instrumented-classes"
    compiled = compile_java(sorted(instr_src.glob("*.java")), instr_classes, classpath)
    if not compiled.success:
        raise ReferenceRunError("Instrumented tests do not compile:\n" + "\n".join(compiled.errors))

    log("  Creating a fresh app container...")
    reset_app(config, app)

    results, snapshots = [], []
    for index, name in enumerate(order):
        result = run_junit(f"{instr_classes}:{classpath}", f"{package}.{name}")
        results.append({"class": name, "passed": result.passed, "seconds": result.time_seconds,
                         "message": result.error_message})
        if not result.passed:
            raise ReferenceRunError(f"Gold test {name} failed in the reference run: {result.error_message}")
        snapshot = root / "snapshots" / f"{index:02d}_{name}.tar"
        snapshot_state(config, app, snapshot)
        snapshots.append(snapshot.name)
        log(f"  [{index:02d}] {name}: PASS ({result.time_seconds}s)")

    missing_html = sorted(n for n in records if not (html_dir / f"{n}.html").exists())
    if missing_html:
        raise ReferenceRunError(f"No HTML captured for: {', '.join(missing_html)}")

    reference = {
        "app": app,
        "image": _docker(config, app)["image"],
        "package": package,
        "order": order,
        "snapshots": snapshots,
        "classpath": classpath,
        "results": results,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (root / "reference.json").write_text(json.dumps(reference, indent=2))
    return reference


def load_reference(config: Config, app: str) -> dict:
    path = reference_dir(config, app) / "reference.json"
    if not path.exists():
        raise ReferenceRunError(
            f"No reference run for {app}. Run 'bewt-pipeline reference-run --app {app}' first."
        )
    return json.loads(path.read_text())


def run_isolated(
    config: Config,
    app: str,
    class_name: str,
    test_source: str,
    work_dir: Path,
) -> tuple[CompileResult, TestResult | None]:
    """Compile `test_source` (a complete test class) and run it on the app
    state saved just before that test in the reference run."""
    reference = load_reference(config, app)
    order = reference["order"]
    if class_name not in order[1:]:
        raise ReferenceRunError(f"{class_name} is not in {app}'s TestSuite order")
    package = reference["package"]

    if work_dir.exists():
        shutil.rmtree(work_dir)
    source_file = work_dir / "src" / package / f"{class_name}.java"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(ensure_assert_imports(test_source))
    compiled = compile_java([source_file], work_dir / "classes", reference["classpath"])
    if not compiled.success:
        return compiled, None

    before = reference["snapshots"][order.index(class_name) - 1]
    ensure_app_running(config, app)
    restore_state(config, app, reference_dir(config, app) / "snapshots" / before)
    result = run_junit(f"{work_dir / 'classes'}:{reference['classpath']}", f"{package}.{class_name}")
    return compiled, result
