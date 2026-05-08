from __future__ import annotations

"""Inject generated assertions back into Java test files."""

import re
import shutil
import subprocess
from pathlib import Path

from ..models import TestRecord

PLACEHOLDER = "// TODO: Insert the missing assertion here"


def inject_assertion(
    record: TestRecord,
    generated_assertion: str,
    variant_source: str,
    output_path: Path,
) -> Path:
    """Write a test file with the generated assertion replacing the placeholder.

    Returns the path to the written file.
    """
    # Replace placeholder with generated assertion
    injected = variant_source.replace(PLACEHOLDER, generated_assertion)

    # Ensure required imports are present
    injected = _ensure_imports(injected, generated_assertion)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(injected)
    return output_path


def _ensure_imports(source: str, assertion: str) -> str:
    """Add missing assertion imports if needed."""
    needed_imports = set()

    if "assertEquals" in assertion and "import static org.junit.Assert.assertEquals" not in source:
        needed_imports.add("import static org.junit.Assert.assertEquals;")
    if "assertTrue" in assertion and "import static org.junit.Assert.assertTrue" not in source:
        needed_imports.add("import static org.junit.Assert.assertTrue;")
    if "assertFalse" in assertion and "import static org.junit.Assert.assertFalse" not in source:
        needed_imports.add("import static org.junit.Assert.assertFalse;")
    if "assertNotNull" in assertion and "import static org.junit.Assert.assertNotNull" not in source:
        needed_imports.add("import static org.junit.Assert.assertNotNull;")

    if not needed_imports:
        return source

    # Insert after existing imports
    lines = source.split('\n')
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith('import '):
            last_import_idx = i

    for imp in needed_imports:
        lines.insert(last_import_idx + 1, imp)

    return '\n'.join(lines)


def _fix_java_version(project_path: Path) -> None:
    """Downgrade maven.compiler.source/target to match the installed JDK."""
    result = subprocess.run(["java", "-version"], capture_output=True, text=True)
    version_str = result.stderr or result.stdout
    m = re.search(r'"(\d+)', version_str)
    if not m:
        return
    jdk_major = m.group(1)

    pom = project_path / "pom.xml"
    if not pom.exists():
        return
    text = pom.read_text()
    text = re.sub(
        r"<maven\.compiler\.source>\d+</maven\.compiler\.source>",
        f"<maven.compiler.source>{jdk_major}</maven.compiler.source>",
        text,
    )
    text = re.sub(
        r"<maven\.compiler\.target>\d+</maven\.compiler\.target>",
        f"<maven.compiler.target>{jdk_major}</maven.compiler.target>",
        text,
    )
    pom.write_text(text)


def prepare_project_copy(
    config,
    app: str,
    record: TestRecord,
    generated_assertion: str,
    variant_source: str,
    treatment: str,
    model: str,
) -> Path:
    """Create a copy of the Maven project with the generated assertion injected.

    All other test files keep their gold-standard assertions so the test suite
    can run in order (tests depend on state from previous tests).
    """
    project_path = config.get_app_project_path(app)
    work_dir = config.output_dir / "runs" / app / model / treatment / record.class_name

    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(project_path, work_dir)

    _fix_java_version(work_dir)

    # Write the injected test file
    test_file = work_dir / "src" / "test" / "java" / "tests" / Path(record.file_path).name
    inject_assertion(record, generated_assertion, variant_source, test_file)

    return work_dir
