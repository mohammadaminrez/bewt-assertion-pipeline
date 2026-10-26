from __future__ import annotations

"""Extract the Java code from LLM responses.

The model's code is taken as written — only markdown fences and prose lines
are removed. Nothing is rebuilt from fragments: if the answer declares a
helper variable before its assertion, that statement is kept, and the
compiler and the test decide whether the answer works.
"""

import re

_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n(.*?)```", re.DOTALL)
_ASSERT_CALL = re.compile(r"\b(?:Assert\.)?assert[A-Z]\w*\s*\(")


def _is_prose(line: str) -> bool:
    """A sentence such as "Here is the assertion:" rather than code."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//"):
        return False
    return not any(ch in stripped for ch in ';(){}="') and " " in stripped


def extract_assertion_from_response(response: str) -> str:
    """Return the code to inject: the fenced block(s) if any, else the reply,
    without prose lines and with common indentation removed."""
    if not response:
        return ""
    blocks = _FENCE.findall(response)
    code = "\n".join(block.strip("\n") for block in blocks) if blocks else response
    lines = [line.rstrip() for line in code.strip("\n").split("\n") if not _is_prose(line)]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents, default=0)
    return "\n".join(line[cut:] for line in lines)


def validate_assertion(code: str) -> bool:
    """The answer must contain at least one JUnit assertion call; everything
    else (syntax, names, types) is left to the compiler."""
    return bool(code) and bool(_ASSERT_CALL.search(code))


def has_extra_statements(code: str) -> bool:
    """True when the answer contains statements other than assertions, e.g. a
    helper variable — allowed, but recorded."""
    text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)  # ignore ; inside strings
    statements = [s.strip() for s in text.split(";") if s.strip()]
    for statement in statements:
        lines = [line for line in statement.split("\n")
                 if line.strip() and not line.strip().startswith("//")]
        body = " ".join(line.strip() for line in lines)
        if body and not _ASSERT_CALL.match(body):
            return True
    return False
