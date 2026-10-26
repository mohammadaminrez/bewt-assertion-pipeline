from __future__ import annotations

"""Generate the test-code variants the treatments are built from."""

from pathlib import Path
from ..models import TestRecord, GherkinScenario
from ..parsing.gherkin_parser import match_gherkin_to_test
from ..parsing.java_parser import PLACEHOLDER


def generate_variant_a(record: TestRecord) -> str:
    """Variant A: Test code with assertion removed, no comment."""
    return record.stripped_source


def gherkin_comment(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
) -> str | None:
    """The `// Assert that ...` comment from the test's Gherkin Then-clauses.

    Returns None when the benchmark has no specification for the test. There
    is deliberately no fallback: deriving a comment from the gold assertion
    would hand the model the answer.
    """
    scenario = match_gherkin_to_test(record.class_name, gherkin_map)
    if scenario is None or not scenario.then_clauses:
        return None
    return scenario.descriptive_comment


def generate_variant_b(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
) -> str:
    """Variant B: stripped test code with the Gherkin comment above the first
    placeholder (unchanged from A when the test has no specification)."""
    source = record.stripped_source
    comment = gherkin_comment(record, gherkin_map)
    if comment is None:
        return source

    lines = source.split("\n")
    for i, line in enumerate(lines):
        if PLACEHOLDER in line:
            indent = line[:len(line) - len(line.lstrip())]
            lines[i:i] = [indent + c for c in comment.split("\n")]
            break
    return "\n".join(lines)


def write_variants(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
    output_dir: Path,
    html_content: str | None = None,
) -> dict[str, Path]:
    """Write the A and B test variants (and the HTML, if any) to disk."""
    app_dir = output_dir / record.app / record.version

    paths = {}
    for variant_name, content in [
        ("A_no_comment", generate_variant_a(record)),
        ("B_with_comment", generate_variant_b(record, gherkin_map)),
    ]:
        variant_dir = app_dir / variant_name
        variant_dir.mkdir(parents=True, exist_ok=True)
        file_path = variant_dir / Path(record.file_path).name
        file_path.write_text(content)
        paths[variant_name] = file_path

    if html_content:
        html_dir = app_dir / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_dir / f"{record.class_name}.html"
        html_path.write_text(html_content)
        paths["html"] = html_path

    return paths
