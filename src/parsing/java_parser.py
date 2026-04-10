from __future__ import annotations

"""Parse Java test files to extract and remove assertions."""

import re
from pathlib import Path

from ..models import AssertionRecord, AssertionType, TestRecord

ASSERTION_PATTERN = re.compile(
    r"(assertEquals|assertTrue|assertFalse|assertNotNull|assertNull)\s*\("
)

ASSERTION_TYPE_MAP = {
    "assertEquals": AssertionType.ASSERT_EQUALS,
    "assertTrue": AssertionType.ASSERT_TRUE,
    "assertFalse": AssertionType.ASSERT_FALSE,
    "assertNotNull": AssertionType.ASSERT_NOT_NULL,
    "assertNull": AssertionType.ASSERT_NULL,
}

PLACEHOLDER = "// TODO: Insert the missing assertion here"


def _find_statement_end(source: str, start: int) -> int:
    """Find the end of a Java statement starting at `start`, handling nested parens."""
    depth = 0
    i = start
    in_string = False
    escape_next = False
    while i < len(source):
        ch = source[i]
        if escape_next:
            escape_next = False
            i += 1
            continue
        if ch == '\\':
            escape_next = True
            i += 1
            continue
        if ch == '"' and not in_string:
            in_string = True
        elif ch == '"' and in_string:
            in_string = False
        elif not in_string:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    # Find the semicolon
                    j = i + 1
                    while j < len(source) and source[j] in (' ', '\t', '\n', '\r'):
                        j += 1
                    if j < len(source) and source[j] == ';':
                        return j + 1
                    return i + 1
        i += 1
    return len(source)


def _find_matching_paren(source: str, open_pos: int) -> int:
    """Find the position of the closing paren that matches the open paren at open_pos."""
    depth = 0
    in_string = False
    escape_next = False
    for i in range(open_pos, len(source)):
        ch = source[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
    return len(source)


def _line_number_at(source: str, char_index: int) -> int:
    """Convert character index to 1-based line number."""
    return source[:char_index].count('\n') + 1


def _parse_assertEquals_args(inner: str) -> tuple[str | None, str | None]:
    """Parse the arguments of assertEquals(expected, actual) or assertEquals(msg, expected, actual)."""
    args = _split_top_level_args(inner)
    if len(args) == 2:
        return args[0].strip(), args[1].strip()
    elif len(args) == 3:
        # With message: assertEquals(msg, expected, actual)
        return args[1].strip(), args[2].strip()
    return None, None


def _split_top_level_args(s: str) -> list[str]:
    """Split a string by commas, respecting parentheses and string literals."""
    args = []
    depth = 0
    current = []
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            current.append(ch)
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            current.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            current.append(ch)
            continue
        if in_string:
            current.append(ch)
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            args.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        args.append(''.join(current))
    return args


def extract_assertions(source: str) -> list[AssertionRecord]:
    """Extract all assertions from Java source code."""
    assertions = []
    for match in ASSERTION_PATTERN.finditer(source):
        method_name = match.group(1)
        start = match.start()
        end = _find_statement_end(source, start)
        full_text = source[start:end].strip()
        start_line = _line_number_at(source, start)
        end_line = _line_number_at(source, end - 1)

        assertion_type = ASSERTION_TYPE_MAP[method_name]
        expected_value = None
        actual_expression = None

        if assertion_type == AssertionType.ASSERT_EQUALS:
            # Extract the inner content between the outermost parens
            paren_start = source.index('(', start)
            # Find the matching closing paren using depth counting
            inner_end = _find_matching_paren(source, paren_start)
            inner = source[paren_start + 1: inner_end].strip()
            expected_value, actual_expression = _parse_assertEquals_args(inner)

        assertions.append(AssertionRecord(
            assertion_type=assertion_type,
            full_text=full_text,
            start_line=start_line,
            end_line=end_line,
            expected_value=expected_value,
            actual_expression=actual_expression,
        ))
    return assertions


def strip_assertions(source: str, assertions: list[AssertionRecord]) -> str:
    """Remove assertions from source and replace with placeholder."""
    if not assertions:
        return source

    lines = source.split('\n')
    # Collect all line ranges to remove (0-indexed)
    lines_to_remove = set()
    for a in assertions:
        for ln in range(a.start_line - 1, a.end_line):
            lines_to_remove.add(ln)

    # Find the first assertion line to insert placeholder
    first_line = min(a.start_line for a in assertions) - 1

    result = []
    placeholder_inserted = False
    for i, line in enumerate(lines):
        if i in lines_to_remove:
            if not placeholder_inserted:
                # Detect indentation from the assertion line
                indent = len(line) - len(line.lstrip())
                result.append(' ' * indent + PLACEHOLDER)
                placeholder_inserted = True
        else:
            result.append(line)

    return '\n'.join(result)


def parse_class_name(source: str) -> str:
    """Extract the class name from Java source."""
    match = re.search(r'public\s+class\s+(\w+)', source)
    return match.group(1) if match else "Unknown"


def parse_method_name(source: str) -> str:
    """Extract the test method name from Java source."""
    match = re.search(r'@Test[^}]*?public\s+void\s+(\w+)', source, re.DOTALL)
    return match.group(1) if match else "Unknown"


def parse_imports(source: str) -> list[str]:
    """Extract all import statements."""
    return re.findall(r'^import\s+.*?;', source, re.MULTILINE)


def resolve_strings_constants(strings_path: Path) -> dict[str, str]:
    """Parse a Strings.java file and return a mapping of constant_name -> value."""
    if not strings_path.exists():
        return {}
    source = strings_path.read_text()
    constants = {}
    for match in re.finditer(
        r'public\s+static\s+final\s+String\s+(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*;',
        source
    ):
        constants[match.group(1)] = match.group(2)
    return constants


def resolve_assertion_constants(
    assertions: list[AssertionRecord],
    constants: dict[str, str],
    prefix: str = "Strings."
) -> None:
    """Resolve Strings.XXX references in assertion expected values."""
    for a in assertions:
        if a.expected_value and a.expected_value.startswith(prefix):
            const_name = a.expected_value[len(prefix):]
            if const_name in constants:
                a.resolved_expected = constants[const_name]


def parse_test_file(
    file_path: Path,
    app: str,
    variant: str,
    version: str,
    constants: dict[str, str] | None = None,
) -> TestRecord:
    """Parse a single Java test file into a TestRecord."""
    source = file_path.read_text()
    assertions = extract_assertions(source)

    if constants:
        resolve_assertion_constants(assertions, constants)

    stripped = strip_assertions(source, assertions)

    return TestRecord(
        app=app,
        variant=variant,
        version=version,
        file_path=str(file_path),
        class_name=parse_class_name(source),
        method_name=parse_method_name(source),
        assertions=assertions,
        imports=parse_imports(source),
        full_source=source,
        stripped_source=stripped,
    )


def parse_all_tests(
    test_dir: Path,
    app: str,
    variant: str,
    version: str,
    constants: dict[str, str] | None = None,
) -> list[TestRecord]:
    """Parse all Java test files in a directory."""
    records = []
    for java_file in sorted(test_dir.glob("*.java")):
        # Skip non-test files
        name = java_file.stem
        if name in ("BaseTest", "TestSuite", "Installer"):
            continue
        try:
            record = parse_test_file(java_file, app, variant, version, constants)
            if record.assertions:  # Only include files that have assertions
                records.append(record)
        except Exception as e:
            print(f"Warning: Failed to parse {java_file}: {e}")
    return records
