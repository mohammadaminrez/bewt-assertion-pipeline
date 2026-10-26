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
from .reference import load_reference, run_isolated

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
