from __future__ import annotations

"""HTML page at the assertion point, for treatments C and D.

Pages are captured by the reference run (see ``execution/reference.py``):
right after each test's first gold assertion group passed, on the app state
produced by running the suite in order.
"""

from ..config import Config


def capture_html_static(config: Config, app: str, test_class_name: str) -> str | None:
    """Load the reference-run HTML for a test, or None if it was not captured."""
    html_path = config.output_dir / "reference" / app / "html" / f"{test_class_name}.html"
    if html_path.exists():
        return html_path.read_text()
    return None
