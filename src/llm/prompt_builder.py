from __future__ import annotations

"""Build prompts for each treatment variant."""

from ..models import TestRecord

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
