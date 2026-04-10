from __future__ import annotations

"""Extract Java assertion code from LLM responses."""

import re


def extract_assertion_from_response(response: str) -> str:
    """Extract the assertion code from an LLM response.

    Handles various response formats:
    - Plain assertion code
    - Code wrapped in markdown code blocks
    - Assertions mixed with explanations
    """
    if not response:
        return ""

    # Try extracting from code blocks first
    code_blocks = re.findall(r'```(?:java)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    if code_blocks:
        # Use the first code block
        code = code_blocks[0].strip()
        assertions = _extract_assertion_lines(code)
        if assertions:
            return assertions

    # Try extracting assertion lines directly
    assertions = _extract_assertion_lines(response)
    if assertions:
        return assertions

    # Last resort: return the response cleaned up
    return _clean_response(response)


def _extract_assertion_lines(text: str) -> str:
    """Extract lines that look like Java assertions."""
    assertion_patterns = [
        r'(assert(?:Equals|True|False|NotNull|Null)\s*\(.*?\)\s*;)',
    ]

    found = []
    for pattern in assertion_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        found.extend(matches)

    if found:
        return '\n'.join(m.strip() for m in found)

    # Try multiline: find assertions that span lines
    lines = text.split('\n')
    result = []
    collecting = False
    current = []
    depth = 0

    for line in lines:
        stripped = line.strip()

        if not collecting:
            if any(stripped.startswith(a) for a in
                   ('assertEquals', 'assertTrue', 'assertFalse', 'assertNotNull', 'assertNull')):
                collecting = True
                current = [stripped]
                depth = stripped.count('(') - stripped.count(')')
                if depth <= 0 and stripped.endswith(';'):
                    result.append(' '.join(current))
                    collecting = False
                    current = []
                    depth = 0
        else:
            current.append(stripped)
            depth += stripped.count('(') - stripped.count(')')
            if depth <= 0 and stripped.endswith(';'):
                result.append(' '.join(current))
                collecting = False
                current = []
                depth = 0

    if result:
        return '\n'.join(result)

    return ""


def _clean_response(text: str) -> str:
    """Clean up a response that doesn't contain recognizable assertions."""
    # Remove common non-code lines
    lines = text.strip().split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip empty lines and markdown
        if not stripped or stripped.startswith('#') or stripped.startswith('//'):
            continue
        # Skip explanation text
        if any(stripped.lower().startswith(w) for w in
               ('the ', 'this ', 'here ', 'note', 'i ', 'you ', 'we ')):
            continue
        cleaned.append(line)

    return '\n'.join(cleaned).strip()


def validate_assertion(assertion: str) -> bool:
    """Basic validation that the extracted assertion looks like valid Java."""
    if not assertion:
        return False

    # Must contain at least one assertion call
    if not re.search(r'assert(?:Equals|True|False|NotNull|Null)\s*\(', assertion):
        return False

    # Must have balanced parentheses
    if assertion.count('(') != assertion.count(')'):
        return False

    # Must end with semicolon
    lines = [l.strip() for l in assertion.strip().split('\n') if l.strip()]
    if lines and not lines[-1].endswith(';'):
        return False

    return True
