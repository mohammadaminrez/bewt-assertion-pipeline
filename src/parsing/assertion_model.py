from __future__ import annotations

"""Data models for parsed test files and assertions."""

from dataclasses import dataclass, field
from enum import Enum


class AssertionType(Enum):
    ASSERT_EQUALS = "assertEquals"
    ASSERT_TRUE = "assertTrue"
    ASSERT_FALSE = "assertFalse"
    ASSERT_NOT_NULL = "assertNotNull"
    ASSERT_NULL = "assertNull"


class ErrorCategory(Enum):
    CORRECT = "correct"
    OVER_ASSERTIVE = "over_assertive"
    UNDER_ASSERTIVE = "under_assertive"
    WRONG_ASSERTION = "wrong_assertion"
    NOT_EXECUTABLE = "not_executable"


@dataclass
class AssertionRecord:
    """A single assertion extracted from a test file."""
    assertion_type: AssertionType
    full_text: str
    start_line: int
    end_line: int
    expected_value: str | None = None
    actual_expression: str | None = None
    resolved_expected: str | None = None  # If expected was a Strings constant

    def to_dict(self) -> dict:
        return {
            "assertion_type": self.assertion_type.value,
            "full_text": self.full_text,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "expected_value": self.expected_value,
            "actual_expression": self.actual_expression,
            "resolved_expected": self.resolved_expected,
        }


@dataclass
class TestRecord:
    """A parsed test file with its assertions and metadata."""
    app: str
    variant: str
    version: str
    file_path: str
    class_name: str
    method_name: str
    assertions: list[AssertionRecord] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    full_source: str = ""
    stripped_source: str = ""  # Source with assertions replaced by placeholder
    page_object_sources: dict[str, str] = field(default_factory=dict)  # class_name -> source

    @property
    def assertion_count(self) -> int:
        return len(self.assertions)

    @property
    def gold_standard(self) -> str:
        """The gold standard assertions as a single string."""
        return "\n".join(a.full_text for a in self.assertions)

    def to_dict(self) -> dict:
        return {
            "app": self.app,
            "variant": self.variant,
            "version": self.version,
            "file_path": self.file_path,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "assertions": [a.to_dict() for a in self.assertions],
            "assertion_count": self.assertion_count,
        }


@dataclass
class GherkinScenario:
    """A parsed Gherkin scenario with its Then-clause."""
    feature_file: str
    scenario_name: str
    then_clauses: list[str] = field(default_factory=list)

    @property
    def descriptive_comment(self) -> str:
        """Convert Then-clauses to a descriptive comment for variant B."""
        lines = []
        for clause in self.then_clauses:
            lines.append(f"// Assert that {clause}")
        return "\n".join(lines)


@dataclass
class ExperimentResult:
    """Result of running a single LLM assertion generation experiment."""
    test_record: TestRecord
    treatment: str  # A, B, or C
    model: str
    prompt: str
    raw_response: str
    generated_assertion: str
    compiles: bool = False
    passes: bool = False
    exact_match: bool = False
    error_category: ErrorCategory = ErrorCategory.NOT_EXECUTABLE
    semantic_similarity: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "app": self.test_record.app,
            "variant": self.test_record.variant,
            "version": self.test_record.version,
            "class_name": self.test_record.class_name,
            "method_name": self.test_record.method_name,
            "treatment": self.treatment,
            "model": self.model,
            "generated_assertion": self.generated_assertion,
            "gold_standard": self.test_record.gold_standard,
            "compiles": self.compiles,
            "passes": self.passes,
            "exact_match": self.exact_match,
            "error_category": self.error_category.value,
            "semantic_similarity": self.semantic_similarity,
            "notes": self.notes,
        }
