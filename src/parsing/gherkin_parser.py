from __future__ import annotations

"""Parse Gherkin feature files to extract Then-clauses for descriptive comments."""

import re
from pathlib import Path

from .assertion_model import GherkinScenario


def parse_feature_file(file_path: Path) -> list[GherkinScenario]:
    """Parse a .feature file and extract scenarios with their Then-clauses."""
    text = file_path.read_text()
    scenarios = []

    # Split by Scenario/Feature blocks
    # Gherkin files in BEWT may or may not have explicit "Scenario:" prefix
    current_scenario_name = file_path.stem
    then_clauses = []

    for line in text.split('\n'):
        stripped = line.strip()

        # Detect scenario name
        if stripped.lower().startswith('scenario:'):
            # Save previous scenario if it has Then clauses
            if then_clauses:
                scenarios.append(GherkinScenario(
                    feature_file=str(file_path),
                    scenario_name=current_scenario_name,
                    then_clauses=then_clauses,
                ))
                then_clauses = []
            current_scenario_name = stripped.split(':', 1)[1].strip()

        elif stripped.lower().startswith('feature:'):
            if not current_scenario_name or current_scenario_name == file_path.stem:
                current_scenario_name = stripped.split(':', 1)[1].strip()

        # Capture Then and And clauses after Then
        elif stripped.lower().startswith('then '):
            then_clauses.append(stripped[5:].strip())

        elif stripped.lower().startswith('and ') and then_clauses:
            # "And" after a "Then" is also part of assertions
            then_clauses.append(stripped[4:].strip())

    # Save last scenario
    if then_clauses:
        scenarios.append(GherkinScenario(
            feature_file=str(file_path),
            scenario_name=current_scenario_name,
            then_clauses=then_clauses,
        ))

    return scenarios


def parse_all_gherkin(gherkin_dir: Path) -> dict[str, GherkinScenario]:
    """Parse all feature files and return a mapping of normalized_name -> GherkinScenario."""
    mapping = {}
    if not gherkin_dir.exists():
        return mapping

    for feature_file in sorted(gherkin_dir.glob("*.feature")):
        scenarios = parse_feature_file(feature_file)
        for scenario in scenarios:
            # Normalize the name for matching with test class names
            key = _normalize_name(feature_file.stem)
            mapping[key] = scenario

    return mapping


def _normalize_name(name: str) -> str:
    """Normalize a feature file name for matching with Java test class names.

    Examples:
        '05_AddProductTest' -> 'addproducttest'
        '01_AddContentTest' -> 'addcontenttest'
        'AddNewProject' -> 'addnewproject'
    """
    # Remove numeric prefix like "05_"
    name = re.sub(r'^\d+_?', '', name)
    return name.lower().replace('_', '').replace('-', '')


def match_gherkin_to_test(
    test_class_name: str,
    gherkin_map: dict[str, GherkinScenario],
) -> GherkinScenario | None:
    """Find the Gherkin scenario matching a test class name."""
    normalized = _normalize_name(test_class_name)
    return gherkin_map.get(normalized)


def generate_comment_from_assertion(assertion_text: str) -> str:
    """Fallback: generate a descriptive comment from the gold-standard assertion itself.

    Used when no Gherkin match is found.
    """
    text = assertion_text.strip()

    if text.startswith("assertEquals"):
        match = re.match(r'assertEquals\s*\(\s*(.+?)\s*,\s*(.+?)\s*\)\s*;?$', text, re.DOTALL)
        if match:
            expected = match.group(1).strip().strip('"')
            actual = match.group(2).strip()
            return f'// Assert that {actual} equals "{expected}"'

    elif text.startswith("assertTrue"):
        match = re.match(r'assertTrue\s*\(\s*(.+?)\s*\)\s*;?$', text, re.DOTALL)
        if match:
            expr = match.group(1).strip()
            return f'// Assert that {expr} is true'

    elif text.startswith("assertFalse"):
        match = re.match(r'assertFalse\s*\(\s*(.+?)\s*\)\s*;?$', text, re.DOTALL)
        if match:
            expr = match.group(1).strip()
            return f'// Assert that {expr} is false'

    return f'// Assert the expected behavior of this test'
