from __future__ import annotations

"""Build prompts for each treatment variant.

Two experiment designs are supported:

* ``cumulative`` (default) — context is added step by step, so each treatment
  contains everything the previous one had plus one more source:
  A=code, B=+comments, C=+HTML, D=+project code (D is already the full
  condition).
* ``singular`` — each treatment isolates a single context source on top of the
  code-only baseline, so their effects can be measured independently:
  A=code, B=code+comments, C=code+HTML, D=code+project code, and E=full (all
  sources together) to measure synergy against the isolated factors.
"""

from dataclasses import dataclass

from ..models import TestRecord

MODE_CUMULATIVE = "cumulative"
MODE_SINGULAR = "singular"
MODES = (MODE_CUMULATIVE, MODE_SINGULAR)


@dataclass(frozen=True)
class Ingredients:
    """Which context sources a treatment's prompt includes."""
    comments: bool   # Gherkin "Then" clauses rendered as // Assert that ... comments
    html: bool       # captured HTML page source at the assertion point
    project: bool    # project code: Page Objects + Strings.java constants


# E is intentionally absent from cumulative: it would duplicate D (the full
# condition), so it is only offered in singular mode.
_CUMULATIVE_INGREDIENTS = {
    "A": Ingredients(comments=False, html=False, project=False),
    "B": Ingredients(comments=True, html=False, project=False),
    "C": Ingredients(comments=True, html=True, project=False),
    "D": Ingredients(comments=True, html=True, project=True),
}

_SINGULAR_INGREDIENTS = {
    "A": Ingredients(comments=False, html=False, project=False),
    "B": Ingredients(comments=True, html=False, project=False),
    "C": Ingredients(comments=False, html=True, project=False),
    "D": Ingredients(comments=False, html=False, project=True),
    "E": Ingredients(comments=True, html=True, project=True),
}

_INGREDIENTS_BY_MODE = {
    MODE_CUMULATIVE: _CUMULATIVE_INGREDIENTS,
    MODE_SINGULAR: _SINGULAR_INGREDIENTS,
}


def valid_treatments(mode: str) -> tuple[str, ...]:
    """Return the treatment codes that are meaningful under the given mode."""
    return tuple(_INGREDIENTS_BY_MODE.get(mode, _CUMULATIVE_INGREDIENTS).keys())


def treatment_ingredients(mode: str, treatment: str) -> Ingredients:
    """Return the context sources a treatment includes under the given mode."""
    table = _INGREDIENTS_BY_MODE.get(mode, _CUMULATIVE_INGREDIENTS)
    treatment = treatment.upper()
    if treatment not in table:
        raise ValueError(
            f"Treatment {treatment} is not valid in {mode} mode "
            f"(valid: {', '.join(table.keys())})"
        )
    return table[treatment]


SYSTEM_PROMPT = """You are an expert Java/Selenium test engineer. You are given a JUnit/Selenium test method where the assertion(s) have been removed and replaced with a TODO placeholder.

Your task: Generate ONLY the missing Java assertion statement(s) that should replace the placeholder.

Rules:
- Output ONLY the Java assertion code (e.g., assertEquals, assertTrue, assertFalse)
- Do NOT include imports, class declarations, or method signatures
- Do NOT include explanations or markdown formatting
- Use JUnit 4 assertions (org.junit.Assert)
- The assertion should verify the expected behavior of the test
- If multiple assertions are needed, output each on its own line"""


def build_prompt_a(record: TestRecord) -> tuple[str, str]:
    """Variant A: Just the stripped test code."""
    user = f"""Complete the following Selenium test by writing the missing assertion(s) where the TODO comment is:

```java
{record.stripped_source}
```"""
    return SYSTEM_PROMPT, user


def build_prompt_b(record: TestRecord, variant_b_source: str) -> tuple[str, str]:
    """Variant B: Stripped test code with descriptive comments."""
    user = f"""Complete the following Selenium test by writing the missing assertion(s) where the TODO comment is.
Descriptive comments have been added to explain what the assertion should check:

```java
{variant_b_source}
```"""
    return SYSTEM_PROMPT, user


def build_prompt_c(
    record: TestRecord,
    variant_c_source: str,
    html_content: str,
) -> tuple[str, str]:
    """Variant C: Stripped test with comments + HTML page content."""
    # Truncate HTML if too large (keep first 8000 chars)
    if len(html_content) > 8000:
        html_content = html_content[:8000] + "\n<!-- ... truncated ... -->"

    user = f"""Complete the following Selenium test by writing the missing assertion(s) where the TODO comment is.
Descriptive comments have been added to explain what the assertion should check.
The HTML content of the web page at the point of assertion is also provided.

Test code:
```java
{variant_c_source}
```

HTML page content at the assertion point:
```html
{html_content}
```"""
    return SYSTEM_PROMPT, user


def build_prompt_d(
    record: TestRecord,
    variant_d_source: str,
    html_content: str | None,
    strings_source: str,
) -> tuple[str, str]:
    """Variant D: Test with comments + HTML + full project source (Strings.java)."""
    user = f"""Complete the following Selenium test by writing the missing assertion(s) where the TODO comment is.
Descriptive comments have been added to explain what the assertion should check.
The HTML content of the web page and the project's utility constants (Strings.java) are also provided.

Test code:
```java
{variant_d_source}
```"""

    if html_content:
        if len(html_content) > 8000:
            html_content = html_content[:8000] + "\n<!-- ... truncated ... -->"
        user += f"""

HTML page content at the assertion point:
```html
{html_content}
```"""

    user += f"""

Project constants (Strings.java):
```java
{strings_source}
```"""

    return SYSTEM_PROMPT, user


def build_prompt_with_page_objects(
    base_prompt: tuple[str, str],
    page_object_sources: dict[str, str],
) -> tuple[str, str]:
    """Enhance a prompt by including relevant Page Object source code."""
    system, user = base_prompt

    if not page_object_sources:
        return system, user

    po_section = "\n\nRelevant Page Object classes:\n"
    for class_name, source in page_object_sources.items():
        po_section += f"\n--- {class_name}.java ---\n```java\n{source}\n```\n"

    return system, user + po_section


def build_assertion_prompt(
    record: TestRecord,
    *,
    variant_source: str,
    include_comments: bool,
    html_content: str | None = None,
    strings_source: str | None = None,
    page_object_sources: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Assemble a prompt from whichever context sources are provided.

    This is mode-agnostic: the caller decides which pieces to pass based on the
    treatment's ingredients (see ``treatment_ingredients``). The descriptive
    text only mentions a source when it is actually included, so the prompt
    stays accurate for both the cumulative and singular designs.
    """
    intro = [
        "Complete the following Selenium test by writing the missing "
        "assertion(s) where the TODO comment is."
    ]
    if include_comments:
        intro.append(
            "Descriptive comments have been added to explain what the "
            "assertion should check."
        )

    user = "\n".join(intro) + f"\n\nTest code:\n```java\n{variant_source}\n```"

    if html_content:
        if len(html_content) > 8000:
            html_content = html_content[:8000] + "\n<!-- ... truncated ... -->"
        user += (
            "\n\nHTML page content at the assertion point:\n"
            f"```html\n{html_content}\n```"
        )

    if strings_source:
        user += f"\n\nProject constants (Strings.java):\n```java\n{strings_source}\n```"

    if page_object_sources:
        user += "\n\nRelevant Page Object classes:\n"
        for class_name, source in page_object_sources.items():
            user += f"\n--- {class_name}.java ---\n```java\n{source}\n```\n"

    return SYSTEM_PROMPT, user
