from __future__ import annotations

"""Ingest DANTE/Crawljax-generated test suites into the pipeline.

DANTE (ICST'20) crawls a web app with a Crawljax fork and generates two suites:

* ``applications/<app>/testsuite-<app>/src/main/java/tests/GeneratedTestSuiteFired.java``
  — plain JUnit 4 + Selenium replays of the crawl paths, one ``testN`` method
  per path, with **no assertions** (the suite this module enriches).
* ``applications/<app>/localhost/crawl-with-inputs/src/test/java/generated/GeneratedTests.java``
  — the Crawljax *oracle suite* (TestNG): the same paths asserted with
  invariants + DOM/screenshot state-equivalence (the comparison baseline).

The crawl output also holds the state-flow graph (``result.json``: states +
edges keyed by the fired element's XPath) and one DOM snapshot per state
(``doms/<state>.html``).

This module:

1. parses the JUnit suite into individual test methods;
2. walks each method's click sequence through the state-flow graph to find the
   state reached at the end of the test (its DOM = the "HTML at the assertion
   point", the treatment-C context);
3. renders a natural-language action trace (the treatment-B comment analog);
4. emits one single-test Java class per method, with the pipeline's standard
   placeholder inserted where the assertion should go — so the existing
   variant/injection/execution machinery works unchanged (results are keyed
   and executed per class, exactly like BEWT tests).
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
    method_name: str           # original method name (e.g. test3)
    class_name: str            # generated standalone class name (e.g. CrawledBluditTest3)
    class_source: str          # standalone class with PLACEHOLDER, no assertion
    final_state: str           # state-flow-graph state reached at the end of the path
    state_dom_path: str | None  # doms/<final_state>.html if it exists
    trace_comment: str         # "// The test ..." natural-language action trace
    visited_states: list[str] = field(default_factory=list)


def _unescape_java(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _normalize_xpath(xpath: str) -> str:
    """Normalize an XPath for matching test actions to graph edges.

    The generated tests and ``result.json`` edges format the same XPath
    differently (case, spaces around ``=``, quote style), e.g.
    ``//a[@id="home"]`` vs ``xpath //A[@id = 'home']``.
    """
    x = xpath.strip()
    x = re.sub(r"^xpath\s+", "", x)
    x = re.sub(r"\s+", "", x)
    x = x.replace('"', "'")
    return x.lower()


def _statements(body: str) -> list[str]:
    """Split a method body into whitespace-normalized Java statements."""
    joined = re.sub(r"\s+", " ", body)
    return [s.strip() + ";" for s in joined.split(";") if s.strip()]


@dataclass
class _Edge:
    target: str
    element_text: str


def load_state_graph(crawl_dir: Path) -> tuple[dict[str, dict], dict[tuple[str, str], _Edge]]:
    """Load states and (source, normalized-xpath) -> edge from result.json."""
    result = json.loads((crawl_dir / "result.json").read_text())
    states = result["states"]
    edges: dict[tuple[str, str], _Edge] = {}
    for edge in result["edges"]:
        key = (edge["from"], _normalize_xpath(edge["id"]))
        text_match = re.search(r"text=([^,}]*)", edge.get("element", "") or "")
        element_text = (edge.get("text") or "").strip() or (
            text_match.group(1).strip() if text_match else ""
        )
        edges[key] = _Edge(target=edge["to"], element_text=element_text)
    return states, edges


def _walk_states(
    statements: list[str],
    edges: dict[tuple[str, str], _Edge],
    initial_state: str = "index",
) -> tuple[str, list[str], list[str]]:
    """Replay click statements through the graph.

    Returns (final_state, visited_states, trace_lines). Unmatched clicks keep
    the current state (Crawljax reset actions and near-duplicate states make a
    perfect walk impossible; the final state is a best-effort anchor).
    """
    current = initial_state
    visited = [current]
    trace: list[str] = []

    for stmt in statements:
        if ".click()" in stmt:
            xpath_match = _XPATH_RE.search(stmt)
            id_match = _ID_RE.search(stmt)
            if xpath_match:
                locator = _normalize_xpath(_unescape_java(xpath_match.group(1)))
            elif id_match:
                locator = _normalize_xpath(f"//*[@id='{id_match.group(1)}']")
            else:
                continue
            edge = edges.get((current, locator))
            if edge is None:
                # Path reset to the index state re-fires earlier events; try
                # matching from the initial state as a fallback.
                edge = edges.get((initial_state, locator))
            if edge is not None:
                label = edge.element_text or locator
                trace.append(f'clicks "{label}"')
                current = edge.target
                visited.append(current)
            else:
                trace.append(f"clicks the element at {locator}")
        elif ".sendKeys(" in stmt:
            keys_match = _SENDKEYS_RE.search(stmt)
            id_match = _ID_RE.search(stmt)
            if keys_match:
                value = _unescape_java(keys_match.group(1))
                target = id_match.group(1) if id_match else "a form field"
                trace.append(f'types "{value}" into {target}')

    return current, visited, trace


def _dedupe_consecutive(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if not out or out[-1] != item:
            out.append(item)
    return out


def _render_trace_comment(trace: list[str], final_state: str) -> str:
    steps = _dedupe_consecutive(trace)
    if not steps:
        summary = "The test loads the application start page."
    else:
        summary = "The test " + "; then ".join(steps) + "."
    return (
        f"// {summary}\n"
        f"// Verify the state of the page reached at this point ({final_state})."
    )


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
    crawl_dir: Path,
    output_dir: Path,
) -> list[CrawledTest]:
    """Parse the DANTE suite and write one placeholdered class per test.

    Args:
        app: pipeline app name (e.g. "bludit").
        suite_path: path to GeneratedTestSuiteFired.java.
        crawl_dir: the crawl output dir holding result.json and doms/.
        output_dir: where the standalone classes and state map are written.

    Returns:
        One CrawledTest per test method, with class files written under
        ``output_dir/tests/`` and a ``state_map.json`` summary.
    """
    source = suite_path.read_text()
    source_class = re.search(r"public class (\w+)", source).group(1)
    _, edges = load_state_graph(crawl_dir)

    tests_dir = output_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    crawled: list[CrawledTest] = []
    for method_name, body in parse_generated_suite(suite_path):
        stmts = _statements(body)
        final_state, visited, trace = _walk_states(stmts, edges)
        dom_path = crawl_dir / "doms" / f"{final_state}.html"

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
            final_state=final_state,
            state_dom_path=str(dom_path) if dom_path.exists() else None,
            trace_comment=_render_trace_comment(trace, final_state),
            visited_states=_dedupe_consecutive(visited),
        ))

    state_map = {
        t.class_name: {
            "method": t.method_name,
            "final_state": t.final_state,
            "dom_path": t.state_dom_path,
            "visited_states": t.visited_states,
            "trace_comment": t.trace_comment,
        }
        for t in crawled
    }
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
