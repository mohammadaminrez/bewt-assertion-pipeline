from __future__ import annotations

"""Ingest DANTE/Crawljax-generated test suites into the pipeline.

DANTE (ICST'20) crawls a web app with a Crawljax fork and generates two suites:

* ``applications/<app>/testsuite-<app>/src/main/java/tests/GeneratedTestSuiteFired.java``
  — plain JUnit 4 + Selenium replays of the crawl paths, one ``testN`` method
  per path, with **no assertions** (the suite this module enriches).
* ``applications/<app>/localhost/crawl-with-inputs/src/test/java/generated/GeneratedTests.java``
  — the Crawljax *oracle suite* (TestNG): the same paths asserted with
  invariants + DOM/screenshot state-equivalence (the comparison baseline).

This module splits the JUnit suite into one single-test class per method,
with the pipeline's standard placeholder where the assertion should go, and
renders a natural-language action trace (the treatment-B comment) from the
test's own statements. The page at the assertion point (treatment C) is not
derived here: it is captured by replaying each test in the crawled reference
run (execution/reference.py), since mapping a test onto the crawl's state
graph is unreliable.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import TestRecord

PLACEHOLDER = "// TODO: Insert the missing assertion here"

# One @Test method in the generated JUnit suite (template output is stable).
_TEST_METHOD_RE = re.compile(
    r"@Test\(\)\s*\n\s*public void (test\d+)\(\)\s*throws Exception\s*\{(.*?)\n\t\}",
    re.DOTALL,
)

_XPATH_RE = re.compile(r'By\.xpath\("((?:[^"\\]|\\.)*)"\)')
_ID_RE = re.compile(r'By\.id\("((?:[^"\\]|\\.)*)"\)')
_SENDKEYS_RE = re.compile(r'\.sendKeys\("((?:[^"\\]|\\.)*)"\)')


@dataclass
class CrawledTest:
    """One crawl path turned into a standalone single-test class."""

    app: str
    source_class: str          # class the method came from (e.g. GeneratedTestSuiteFired)
    method_name: str           # original method name (e.g. test03)
    class_name: str            # generated standalone class name (e.g. CrawledBluditTest03)
    class_source: str          # standalone class with PLACEHOLDER, no assertion
    trace_comment: str         # "// The test ..." natural-language action trace


def _unescape_java(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _statements(body: str) -> list[str]:
    """Split a method body into whitespace-normalized Java statements."""
    joined = re.sub(r"\s+", " ", body)
    return [s.strip() + ";" for s in joined.split(";") if s.strip()]


_GET_RE = re.compile(r'driver\.get\("((?:[^"\\]|\\.)*)"\)')


def _describe_statements(statements: list[str]) -> list[str]:
    """What the test does, read from its own statements (no guessing)."""
    steps = []
    for stmt in statements:
        locator = None
        id_match, xpath_match = _ID_RE.search(stmt), _XPATH_RE.search(stmt)
        if id_match:
            locator = f'the element with id "{_unescape_java(id_match.group(1))}"'
        elif xpath_match:
            locator = f"the element {_unescape_java(xpath_match.group(1))}"
        get_match = _GET_RE.search(stmt)
        keys_match = _SENDKEYS_RE.search(stmt)
        if get_match:
            steps.append(f'opens {_unescape_java(get_match.group(1))}')
        elif ".click()" in stmt and locator:
            steps.append(f"clicks {locator}")
        elif keys_match and locator:
            steps.append(f'types "{_unescape_java(keys_match.group(1))}" into {locator}')
        elif "closeOtherWindows" in stmt:
            steps.append("closes the extra browser tab")
    return steps


def _dedupe_consecutive(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if not out or out[-1] != item:
            out.append(item)
    return out


def _render_trace_comment(steps: list[str]) -> str:
    steps = _dedupe_consecutive(steps)
    summary = "The test " + "; then ".join(steps) + "." if steps else \
        "The test loads the application start page."
    return f"// {summary}\n// Verify the state of the page reached at this point."


def _standalone_class(
    original_source: str,
    body: str,
    class_name: str,
    method_name: str,
) -> str:
    """Build a compilable single-test class with the placeholder appended.

    Reuses the generated suite's package, imports, and setup/teardown scaffolding
    so the class drops into ``testsuite-<app>`` without further changes.
    """
    header_end = original_source.index("public class ")
    header = original_source[:header_end]

    scaffold = (
        f"public class {class_name} {{\n"
        "\n"
        "\tprivate static WebDriver driver;\n"
        "\n"
        "\t@BeforeClass\n"
        "\tpublic static void oneTimeSetUp() throws Exception {\n"
        "\t\tdriver = DriverProvider.getInstance().getDriver();\n"
        "\t\tdriver.get(Properties.app_url);\n"
        "\t}\n"
        "\n"
        "\t@AfterClass\n"
        "\tpublic static void oneTimeTearDown() {\n"
        "\t\tdriver.quit();\n"
        "\t}\n"
        "\n"
        "\t@Test()\n"
        f"\tpublic void {method_name}() throws Exception {{"
        f"{body.rstrip()}\n"
        f"\t\t{PLACEHOLDER}\n"
        "\t}\n"
        "}\n"
    )
    return header + scaffold


def parse_generated_suite(suite_path: Path) -> list[tuple[str, str]]:
    """Return (method_name, body) for each test method in the JUnit suite."""
    source = suite_path.read_text()
    return [(m.group(1), m.group(2)) for m in _TEST_METHOD_RE.finditer(source)]


def ingest_dante_suite(
    app: str,
    suite_path: Path,
    output_dir: Path,
) -> list[CrawledTest]:
    """Parse the DANTE suite and write one placeholdered class per test.

    Args:
        app: pipeline app name (e.g. "bludit").
        suite_path: path to GeneratedTestSuiteFired.java.
        output_dir: where the standalone classes and state map are written.

    Returns:
        One CrawledTest per test method, with class files written under
        ``output_dir/tests/`` and a ``state_map.json`` summary.
    """
    source = suite_path.read_text()
    source_class = re.search(r"public class (\w+)", source).group(1)

    tests_dir = output_dir / "tests"
    if tests_dir.exists():
        for old in tests_dir.glob("*.java"):
            old.unlink()
    tests_dir.mkdir(parents=True, exist_ok=True)

    crawled: list[CrawledTest] = []
    for method_name, body in parse_generated_suite(suite_path):
        index = re.sub(r"\D", "", method_name)
        class_name = f"Crawled{app.capitalize()}Test{index}"
        class_source = _standalone_class(source, body, class_name, method_name)
        (tests_dir / f"{class_name}.java").write_text(class_source)
        crawled.append(CrawledTest(
            app=app,
            source_class=source_class,
            method_name=method_name,
            class_name=class_name,
            class_source=class_source,
            trace_comment=_render_trace_comment(_describe_statements(_statements(body))),
        ))

    state_map = {t.class_name: {"method": t.method_name, "trace_comment": t.trace_comment}
                 for t in crawled}
    (output_dir / "state_map.json").write_text(json.dumps(state_map, indent=2))
    return crawled


def to_test_records(crawled: list[CrawledTest], version: str) -> list[TestRecord]:
    """Convert CrawledTests to pipeline TestRecords (no gold assertions)."""
    records = []
    for t in crawled:
        records.append(TestRecord(
            app=t.app,
            variant="crawled",
            version=version,
            file_path="",
            class_name=t.class_name,
            method_name=t.method_name,
            assertions=[],
            full_source=t.class_source,
            stripped_source=t.class_source,
        ))
    return records


def crawled_variant_source(record: TestRecord, trace_comment: str | None) -> str:
    """Variant with the action-trace comment above the placeholder (treatment B analog)."""
    if not trace_comment:
        return record.stripped_source
    indented = "\n".join(
        "\t\t" + line.lstrip() for line in trace_comment.splitlines()
    )
    return record.stripped_source.replace(
        f"\t\t{PLACEHOLDER}", f"{indented}\n\t\t{PLACEHOLDER}"
    )
