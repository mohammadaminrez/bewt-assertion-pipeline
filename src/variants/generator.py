from __future__ import annotations

"""Generate test variants A, B, C from parsed test records."""

from pathlib import Path
from ..models import TestRecord, GherkinScenario
from ..parsing.gherkin_parser import match_gherkin_to_test, generate_comment_from_assertion

PLACEHOLDER = "// TODO: Insert the missing assertion here"


def generate_variant_a(record: TestRecord) -> str:
    """Variant A: Test code with assertion removed, no comment."""
    return record.stripped_source


def generate_variant_b(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
) -> str:
    """Variant B: Test code with assertion removed + descriptive comment."""
    source = record.stripped_source

    # Try to match with Gherkin scenario
    scenario = match_gherkin_to_test(record.class_name, gherkin_map)

    if scenario:
        comment = scenario.descriptive_comment
    else:
        # Fallback: generate comment from the gold-standard assertion
        comments = []
        for a in record.assertions:
            comments.append(generate_comment_from_assertion(a.full_text))
        comment = "\n".join(comments)

    # Replace the placeholder with comment + placeholder
    return source.replace(
        PLACEHOLDER,
        f"{comment}\n{_get_indent(source, PLACEHOLDER)}{PLACEHOLDER}"
    )


def generate_variant_c(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
    html_content: str | None = None,
) -> tuple[str, str | None]:
    """Variant C: Same as B, but HTML is provided separately for the LLM prompt.

    Returns (test_source, html_content).
    """
    test_source = generate_variant_b(record, gherkin_map)
    return test_source, html_content


def _get_indent(source: str, marker: str) -> str:
    """Get the indentation of a marker line in the source."""
    for line in source.split('\n'):
        if marker in line:
            return ' ' * (len(line) - len(line.lstrip()))
    return '\t\t'


def write_variants(
    record: TestRecord,
    gherkin_map: dict[str, GherkinScenario],
    output_dir: Path,
    html_content: str | None = None,
) -> dict[str, Path]:
    """Write all three variants to disk and return their paths."""
    app_dir = output_dir / record.app / record.version

    paths = {}
    for variant_name, content in [
        ("A_no_comment", generate_variant_a(record)),
        ("B_with_comment", generate_variant_b(record, gherkin_map)),
        ("C_with_html", generate_variant_c(record, gherkin_map, html_content)[0]),
    ]:
        variant_dir = app_dir / variant_name
        variant_dir.mkdir(parents=True, exist_ok=True)
        file_path = variant_dir / Path(record.file_path).name
        file_path.write_text(content)
        paths[variant_name] = file_path

    # Also save HTML if available
    if html_content:
        html_dir = app_dir / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_dir / f"{record.class_name}.html"
        html_path.write_text(html_content)
        paths["html"] = html_path

    return paths
