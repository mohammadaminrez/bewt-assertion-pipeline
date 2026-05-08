from __future__ import annotations

"""Capture HTML pages from running web applications for variant C."""

import shutil
import subprocess
from pathlib import Path
from ..config import Config


def capture_html_for_app(
    config: Config,
    app: str,
    variant: str | None = None,
    version: str | None = None,
    on_progress: callable | None = None,
) -> dict[str, str]:
    """Capture HTML page sources by running instrumented tests.

    Strategy:
    1. Copy the project to a temp directory
    2. Modify BaseTest to save page source before assertions
    3. Run the test suite
    4. Collect the saved HTML files

    Returns a mapping of test_class_name -> html_content.
    """
    def _log(msg: str):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    project_path = config.get_app_project_path(app, variant, version)
    capture_dir = config.output_dir / "html_capture" / app
    capture_dir.mkdir(parents=True, exist_ok=True)

    # Create instrumented copy
    _log("  Copying project...")
    instrumented_path = config.output_dir / "instrumented" / app
    if instrumented_path.exists():
        shutil.rmtree(instrumented_path)
    shutil.copytree(project_path, instrumented_path)

    # Fix Java compiler target to match installed JDK
    _fix_java_version(instrumented_path)

    # Inject page source capture into BaseTest
    _log("  Injecting HTML capture hooks...")
    _inject_capture_hook(instrumented_path, capture_dir)

    # Run tests to capture HTML
    if not shutil.which("mvn"):
        raise RuntimeError(
            "Maven (mvn) is required for HTML capture but was not found.\n"
            "  Install Maven: https://maven.apache.org/install.html"
        )

    # Find the test package name for the -Dtest argument
    java_root = instrumented_path / "src" / "test" / "java"
    test_pkg = ""
    for candidate in java_root.iterdir():
        if candidate.is_dir() and (candidate / "TestSuite.java").exists():
            test_pkg = candidate.name + "."
            break

    _log("  Running Maven tests (this may take a few minutes)...")
    process = subprocess.Popen(
        ["mvn", "test", f"-Dtest={test_pkg}TestSuite", "-pl", "."],
        cwd=str(instrumented_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    for line in process.stdout:
        line = line.rstrip()
        # Show key Maven events
        if "Downloading from" in line and "Downloading" not in (on_progress and "shown" or ""):
            _log("  Downloading dependencies...")
            on_progress and setattr(on_progress, "shown", True)  # only show once
        elif "Running " in line and "tests." in line.lower():
            test_name = line.split("Running ")[-1].strip()
            _log(f"  Running: {test_name}")
        elif "Tests run:" in line:
            _log(f"  {line.strip()}")
        elif "BUILD SUCCESS" in line:
            _log("  Maven build succeeded")
        elif "BUILD FAILURE" in line:
            _log("  Maven build had failures (some test failures are expected)")

    process.wait()

    # Collect captured HTML files
    html_map = {}
    for html_file in capture_dir.glob("*.html"):
        test_name = html_file.stem
        html_map[test_name] = html_file.read_text()

    return html_map


def _fix_java_version(project_path: Path) -> None:
    """Downgrade maven.compiler.source/target to match the installed JDK."""
    import re as _re
    result = subprocess.run(["java", "-version"], capture_output=True, text=True)
    version_str = result.stderr or result.stdout
    m = _re.search(r'"(\d+)', version_str)
    if not m:
        return
    jdk_major = m.group(1)

    pom = project_path / "pom.xml"
    if not pom.exists():
        return
    text = pom.read_text()
    text = _re.sub(
        r"<maven\.compiler\.source>\d+</maven\.compiler\.source>",
        f"<maven.compiler.source>{jdk_major}</maven.compiler.source>",
        text,
    )
    text = _re.sub(
        r"<maven\.compiler\.target>\d+</maven\.compiler\.target>",
        f"<maven.compiler.target>{jdk_major}</maven.compiler.target>",
        text,
    )
    pom.write_text(text)


def _inject_capture_hook(project_path: Path, capture_dir: Path) -> None:
    """Modify test files to capture page source before assertions.

    Adds a line to save driver.getPageSource() right before assertion lines.
    """
    java_root = project_path / "src" / "test" / "java"
    # Find the actual test package (could be "tests", "mediawiki", app name, etc.)
    test_dir = None
    for candidate in java_root.iterdir():
        if candidate.is_dir() and any(candidate.glob("*.java")):
            test_dir = candidate
            break
    if test_dir is None:
        return

    for java_file in test_dir.glob("*.java"):
        if java_file.stem in ("BaseTest", "TestSuite", "Installer"):
            continue

        source = java_file.read_text()
        class_name = java_file.stem

        # Add import — detect the actual package name
        if "java.nio.file" not in source:
            pkg_name = test_dir.name
            source = source.replace(
                f"package {pkg_name};",
                f"package {pkg_name};\n\nimport java.nio.file.Files;\nimport java.nio.file.Paths;",
            )

        # Insert capture before each assertion
        capture_line = (
            f'\t\ttry {{ Files.write(Paths.get("{capture_dir}/{class_name}.html"), '
            f'driver.getPageSource().getBytes()); }} catch (Exception _e) {{}}\n'
        )

        # Insert before assertion lines
        lines = source.split('\n')
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if any(stripped.startswith(a) for a in ('assertEquals', 'assertTrue', 'assertFalse', 'assertNotNull')):
                # Only insert once per test (before the first assertion)
                if capture_line not in '\n'.join(new_lines):
                    new_lines.append(capture_line)
            new_lines.append(line)

        java_file.write_text('\n'.join(new_lines))


def capture_html_static(
    config: Config,
    app: str,
    test_class_name: str,
    variant: str | None = None,
    version: str | None = None,
) -> str | None:
    """Try to load previously captured HTML for a test."""
    html_path = config.output_dir / "html_capture" / app / f"{test_class_name}.html"
    if html_path.exists():
        return html_path.read_text()
    return None
