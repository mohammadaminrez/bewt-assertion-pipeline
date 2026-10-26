from __future__ import annotations

"""Result types for compiling and running Java tests."""

from dataclasses import dataclass


@dataclass
class TestResult:
    test_class: str
    test_method: str
    passed: bool
    failed: bool
    errored: bool
    skipped: bool
    error_message: str = ""
    error_type: str = ""
    time_seconds: float = 0.0


@dataclass
class CompileResult:
    success: bool
    errors: list[str]
