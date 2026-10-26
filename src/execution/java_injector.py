from __future__ import annotations

"""Inject generated assertions back into Java test files."""

import re
import subprocess
from pathlib import Path

from ..models import TestRecord
from ..parsing.java_parser import PLACEHOLDER

_MARKER = re.compile(r"^\s*//\s*\((\d+)\)\s*$")
_STATIC_ASSERT_CALL = re.compile(r"(?<![\w.])(assert[A-Z]\w*)\s*\(")
_IPV4_URL = re.compile(r"(https?://)(?:\d{1,3}\.){3}\d{1,3}(:\d+)?")


def fill_placeholders(source: str, answer: str) -> tuple[str, str]:
    """Replace the placeholder(s) in `source` with the model's answer.

    With several numbered placeholders, the answer's `// (n)` marker lines
    route each group of code to placeholder n. Returns the filled source and a
    note describing any deviation from that format (empty when none).
    """
    lines = source.split("\n")
    slots = [i for i, line in enumerate(lines) if PLACEHOLDER in line]
    if not slots:
        return source, "source has no placeholder"

    notes = []
    groups: dict[int, list[str]] = {}
    current = 1
    answer_lines = answer.split("\n")
    if len(slots) > 1 and not any(_MARKER.match(line) for line in answer_lines):
        notes.append("answer has no // (n) markers; all code placed at placeholder (1)")
    for line in answer_lines:
        marker = _MARKER.match(line)
        if marker and len(slots) > 1:
            current = int(marker.group(1))
            continue
        groups.setdefault(current, []).append(line)

    unknown = sorted(n for n in groups if not 1 <= n <= len(slots))
    if unknown:
        notes.append(f"code under marker(s) {unknown} has no placeholder and was dropped")
    empty = [n for n in range(1, len(slots) + 1) if not any(l.strip() for l in groups.get(n, []))]
    if empty and len(slots) > 1:
        notes.append(f"no answer for placeholder(s) {empty}")

    for n, idx in reversed(list(enumerate(slots, start=1))):
        indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
        code = [indent + line if line.strip() else "" for line in groups.get(n, [])]
        lines[idx:idx + 1] = code
    return "\n".join(lines), "; ".join(notes)


# Classes a generated answer may use without importing them: the prompt
# forbids imports, so the harness supplies them (simple name -> package).
_KNOWN_CLASSES = {
    "Assert": "org.junit",
    "By": "org.openqa.selenium",
    "WebElement": "org.openqa.selenium",
    "Keys": "org.openqa.selenium",
    "List": "java.util",
}


def ensure_assert_imports(source: str) -> str:
    """Add the imports a generated answer needs but the test lacks: a static
    import per unqualified assertX(...) call, and the well-known classes in
    _KNOWN_CLASSES. Wildcard imports count; import lines are not usage."""
    body = "\n".join(l for l in source.split("\n") if not l.strip().startswith(("import ", "package ")))
    imports = []
    if not re.search(r"import\s+static\s+org\.junit\.Assert\.\*\s*;", source):
        imports += [
            f"import static org.junit.Assert.{name};"
            for name in sorted(set(_STATIC_ASSERT_CALL.findall(body)))
            if not re.search(rf"import\s+static\s+org\.junit\.Assert\.{name}\s*;", source)
        ]
    for name, package in _KNOWN_CLASSES.items():
        used = re.search(rf"(?<![\w.]){name}\s*[.<(\s]", body)
        if used and not re.search(rf"import\s+{re.escape(package)}\.({name}|\*)\s*;", source) \
                and not re.search(rf"import\s+[\w.]+\.{name}\s*;", source):
            imports.append(f"import {package}.{name};")
    if not imports:
        return source

    lines = source.split("\n")
    anchor = max((i for i, line in enumerate(lines) if line.strip().startswith("import ")), default=None)
    if anchor is None:
        anchor = next(i for i, line in enumerate(lines) if line.strip().startswith("package "))
    lines[anchor + 1:anchor + 1] = imports
    return "\n".join(lines)


def inject_assertion(
    record: TestRecord,
    generated_assertion: str,
    variant_source: str,
    output_path: Path,
) -> Path:
    """Write a test file with the generated assertion replacing the placeholder(s).

    Returns the path to the written file.
    """
    injected, _ = fill_placeholders(variant_source, generated_assertion)
    injected = ensure_assert_imports(injected)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(injected)
    return output_path


def localize_base_url(project_path: Path, base_url: str) -> list[Path]:
    """Point hard-coded LAN IPs in the test sources at the configured app URL
    (e.g. BEWT's Bludit BaseTest uses http://192.168.1.141:8080). Returns the
    files changed."""
    target = re.match(r"(https?://[^/]+)", base_url).group(1)
    changed = []
    for java_file in (project_path / "src" / "test" / "java").rglob("*.java"):
        text = java_file.read_text()
        new = _IPV4_URL.sub(target, text)
        if new != text:
            java_file.write_text(new)
            changed.append(java_file)
    return changed


def fix_java_version(project_path: Path) -> None:
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
