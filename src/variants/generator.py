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
    """The test's whole Gherkin feature file as a `//` comment block.

    Returns None when the benchmark has no specification for the test. There
    is deliberately no fallback: deriving a comment from the gold assertion
    would hand the model the answer.
    """
    scenario = match_gherkin_to_test(record.class_name, gherkin_map, record.app)
    if scenario is None or not scenario.text.strip():
        return None
    return scenario.specification_comment


def generate_variant_b(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
) -> str:
    """Variant B: stripped test code with the specification comment above the
    test method's @Test annotation (above the first placeholder if there is
    none); unchanged from A when the test has no specification."""
    source = record.stripped_source
    comment = gherkin_comment(record, gherkin_map)
    if comment is None:
        return source

    lines = source.split("\n")
    anchor = next((i for i, l in enumerate(lines) if l.strip().startswith("@Test")), None)
    if anchor is None:
        anchor = next(i for i, l in enumerate(lines) if PLACEHOLDER in l)
    line = lines[anchor]
    indent = line[:len(line) - len(line.lstrip())]
    lines[anchor:anchor] = [indent + c for c in comment.split("\n")]
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
