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
) -> dict[str, str]:
    """Capture HTML page sources by running instrumented tests.

    Strategy:
    1. Copy the project to a temp directory
    2. Modify BaseTest to save page source before assertions
    3. Run the test suite
    4. Collect the saved HTML files

    Returns a mapping of test_class_name -> html_content.
    """
    project_path = config.get_app_project_path(app, variant, version)
    capture_dir = config.output_dir / "html_capture" / app
    capture_dir.mkdir(parents=True, exist_ok=True)

    # Create instrumented copy
    instrumented_path = config.output_dir / "instrumented" / app
    if instrumented_path.exists():
        shutil.rmtree(instrumented_path)
    shutil.copytree(project_path, instrumented_path)

    # Inject page source capture into BaseTest
    _inject_capture_hook(instrumented_path, capture_dir)

    # Run tests to capture HTML
    if not shutil.which("mvn"):
        raise RuntimeError(
            "Maven (mvn) is required for HTML capture but was not found.\n"
            "  Install Maven: https://maven.apache.org/install.html"
        )

    result = subprocess.run(
        ["mvn", "test", "-Dtest=TestSuite", "-pl", "."],
        cwd=str(instrumented_path),
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        print(f"Warning: Test run for HTML capture had failures (expected): {result.returncode}")

    # Collect captured HTML files
    html_map = {}
    for html_file in capture_dir.glob("*.html"):
        test_name = html_file.stem
        html_map[test_name] = html_file.read_text()

    return html_map


def _inject_capture_hook(project_path: Path, capture_dir: Path) -> None:
    """Modify test files to capture page source before assertions.

    Adds a line to save driver.getPageSource() right before assertion lines.
    """
    test_dir = project_path / "src" / "test" / "java" / "tests"

    for java_file in test_dir.glob("*.java"):
        if java_file.stem in ("BaseTest", "TestSuite", "Installer"):
            continue

        source = java_file.read_text()
        class_name = java_file.stem

        # Add import
        if "java.nio.file" not in source:
            source = source.replace(
                "package tests;",
                "package tests;\n\nimport java.nio.file.Files;\nimport java.nio.file.Paths;",
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
