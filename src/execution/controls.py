from __future__ import annotations

"""Controls that validate the execution harness itself.

- Positive control: the gold assertion, put back through the same path as a
  generated one (placeholders, injection, isolated run), must pass.
- Negative control: the logical negation of the gold assertion must compile
  and fail. A harness that lets it pass is not checking the assertion.
"""

import re
from typing import Callable

from ..config import Config
from ..parsing.java_parser import parse_all_tests
from .java_injector import fill_placeholders
from .reference import load_reference, reference_dir, run_isolated

_NEGATIONS = {
    "assertEquals": "assertNotEquals",
    "assertNotEquals": "assertEquals",
    "assertTrue": "assertFalse",
    "assertFalse": "assertTrue",
    "assertNull": "assertNotNull",
    "assertNotNull": "assertNull",
}


def negate(gold: str) -> str:
    """Swap every assertion for its logical opposite."""
    return re.sub(
        r"\b(" + "|".join(_NEGATIONS) + r")\s*\(",
        lambda m: _NEGATIONS[m.group(1)] + "(",
        gold,
    )


def run_controls(config: Config, app: str, log: Callable[[str], None] = print) -> list[dict]:
    reference = load_reference(config, app)
    test_dir = config.get_app_test_path(app)
    records = {r.class_name: r for r in parse_all_tests(test_dir, app, "", "")}
    rows = []
    for name in reference["order"][1:]:
        record = records[name]
        row = {"class": name}
        for kind, answer, expect_pass in (
            ("positive", record.gold_standard, True),
            ("negative", negate(record.gold_standard), False),
        ):
            source, note = fill_placeholders(record.stripped_source, answer)
            work = config.output_dir / "controls" / app / kind / name
            compiled, result = run_isolated(config, app, name, source, work)
            passed = bool(result and result.passed)
            ok = compiled.success and passed == expect_pass
            row[kind] = {
                "ok": ok,
                "compiles": compiled.success,
                "passed": passed,
                "note": note,
                "message": (result.error_message if result else "; ".join(compiled.errors))[:300],
            }
        rows.append(row)
        log(f"  {name:26} positive={'OK' if row['positive']['ok'] else 'WRONG'}"
            f"  negative={'OK' if row['negative']['ok'] else 'WRONG'}")
    return rows


def _page_title(html: str) -> str | None:
    """The title as the browser reports it (entities decoded, whitespace collapsed)."""
    from html import unescape
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return " ".join(unescape(match.group(1)).split()) if match else None


def _java_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_crawled_controls(config: Config, app: str, log: Callable[[str], None] = print) -> list[dict]:
    """Controls for crawler-generated tests, which have no gold assertion.

    Positive: the page title captured at the assertion point in the reference
    run must equal driver.getTitle() when the test runs isolated on its saved
    state. Negative: the negation must fail. Together they check that the
    restore, replay and judging reproduce the reference state exactly.
    """
    reference = load_reference(config, app, "crawled")
    root = reference_dir(config, app, "crawled")
    tests_dir = config.output_dir / "crawled" / app / "tests"
    rows = []
    for name in reference["order"][1:]:
        title = _page_title((root / "html" / f"{name}.html").read_text())
        row = {"class": name, "title": title}
        if title is None:
            row["positive"] = row["negative"] = {"ok": False, "compiles": False, "passed": False,
                                                 "note": "no <title> in captured page", "message": ""}
            rows.append(row)
            continue
        source_template = (tests_dir / f"{name}.java").read_text()
        for kind, answer, expect_pass in (
            ("positive", f"assertEquals({_java_string(title)}, driver.getTitle());", True),
            ("negative", f"assertNotEquals({_java_string(title)}, driver.getTitle());", False),
        ):
            source, note = fill_placeholders(source_template, answer)
            work = config.output_dir / "controls-crawled" / app / kind / name
            compiled, result = run_isolated(config, app, name, source, work, source="crawled")
            passed = bool(result and result.passed)
            row[kind] = {
                "ok": compiled.success and passed == expect_pass,
                "compiles": compiled.success,
                "passed": passed,
                "note": note,
                "message": (result.error_message if result else "; ".join(compiled.errors))[:300],
            }
        rows.append(row)
        log(f"  {name:22} title={title!r:40.40} positive={'OK' if row['positive']['ok'] else 'WRONG'}"
            f"  negative={'OK' if row['negative']['ok'] else 'WRONG'}")
    return rows
