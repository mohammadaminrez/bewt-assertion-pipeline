from __future__ import annotations

"""Shared data models for the BEWT assertion pipeline."""

from dataclasses import dataclass, field
from enum import Enum


class AssertionType(Enum):
    ASSERT_EQUALS = "assertEquals"
    ASSERT_TRUE = "assertTrue"
    ASSERT_FALSE = "assertFalse"
    ASSERT_NOT_NULL = "assertNotNull"
    ASSERT_NULL = "assertNull"


class ExecutionOutcome(Enum):
    """What happened when the generated assertion was actually run.

    JUnit reports two distinct kinds of non-pass, and they mean opposite things
    here: a *failure* is the assertion rejecting a value (a real verdict about
    the assertion), while an *error* is the test blowing up before it ever
    reached the assertion (app down, missing precondition, stale locator).
    Collapsing them into one boolean makes a bad assertion indistinguishable
    from a broken environment.
    """
    PASSED = "passed"            # ran, assertion held
    FAILED = "failed"            # ran, assertion rejected the value
    ERRORED = "errored"          # threw before reaching a verdict
    NOT_COMPILED = "not_compiled"  # never ran
    NOT_RUN = "not_run"          # execution was not requested

    @property
    def functional_bucket(self) -> str:
        """The Passed / Failed / Not working bucket used for reporting."""
        if self is ExecutionOutcome.PASSED:
            return "passed"
        if self is ExecutionOutcome.FAILED:
            return "failed"
        return "not_working"


class ErrorCategory(Enum):
    # Assigned by review only — the pipeline never guesses these.
    CORRECT = "correct"
    OVER_ASSERTIVE = "over_assertive"
    UNDER_ASSERTIVE = "under_assertive"
    WRONG_ASSERTION = "wrong_assertion"
    # Assigned mechanically: the assertion could not be run at all.
    NOT_EXECUTABLE = "not_executable"
    # Ran, but nobody has judged its quality yet.
    PENDING = "pending"


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
    treatment: str  # A, B, C, D, or E
    model: str
    prompt: str
    raw_response: str
    generated_assertion: str
    compiles: bool = False
    passes: bool = False
    outcome: ExecutionOutcome = ExecutionOutcome.NOT_RUN
    error_type: str = ""  # e.g. org.junit.ComparisonFailure, NoSuchElementException
    exact_match: bool = False
    error_category: ErrorCategory = ErrorCategory.PENDING
    semantic_similarity: float = 0.0
    notes: str = ""
    mode: str = "cumulative"  # experiment design: cumulative | singular
    manual_error_category: str = ""  # filled in by review, via import-excel

    @property
    def final_category(self) -> str:
        """The category to report: the reviewed label when one exists."""
        if self.manual_error_category:
            return self.manual_error_category
        return self.error_category.value

    def set_outcome(self, outcome: ExecutionOutcome, error_type: str = "") -> None:
        """Record an execution outcome, keeping compiles/passes consistent."""
        self.outcome = outcome
        self.error_type = error_type
        self.compiles = outcome not in (
            ExecutionOutcome.NOT_COMPILED, ExecutionOutcome.NOT_RUN
        )
        self.passes = outcome is ExecutionOutcome.PASSED

    def to_dict(self) -> dict:
        return {
            "app": self.test_record.app,
            "variant": self.test_record.variant,
            "version": self.test_record.version,
            "class_name": self.test_record.class_name,
            "method_name": self.test_record.method_name,
            "treatment": self.treatment,
            "mode": self.mode,
            "model": self.model,
            "generated_assertion": self.generated_assertion,
            "gold_standard": self.test_record.gold_standard,
            "compiles": self.compiles,
            "passes": self.passes,
            "outcome": self.outcome.value,
            "functional_bucket": self.outcome.functional_bucket,
            "error_type": self.error_type,
            "exact_match": self.exact_match,
            "error_category": self.error_category.value,
            "manual_error_category": self.manual_error_category,
            "final_category": self.final_category,
            "semantic_similarity": self.semantic_similarity,
            "notes": self.notes,
        }
