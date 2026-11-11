from __future__ import annotations

"""Parse Gherkin feature files to extract Then-clauses for descriptive comments."""

import re
from pathlib import Path

from ..models import GherkinScenario


# A Then/And step that describes something the user does, not something to
# check. BEWT writes the teardown after the last check this way, e.g.
#   Given the previous assertion passed
#   Then the user clicks on the "A" icon ...
#   And clicks the "Logout" link
# (all of Kanboard, most of Claroline). "clciks" is a typo in Joomla's specs.
_ACTION_STEP = re.compile(
    r'^(the user\s+)?(clicks|clciks|enters|selects|confirms|types|checks|unchecks|'
    r'goes|navigates|opens|presses|scrolls|logs)\b',
    re.IGNORECASE,
)


def _is_assertion_step(text: str) -> bool:
    return not _ACTION_STEP.match(text)


def parse_feature_file(file_path: Path) -> list[GherkinScenario]:
    """Parse a .feature file and extract scenarios with their Then-clauses.

    Steps are read by keyword: ``And``/``But`` continue the previous
    keyword, so an ``And`` under a ``When`` is an action even after an
    earlier ``Then``. Then-steps that describe a user action (the logout
    teardown BEWT appends after the last check) are not assertions.
    """
    text = file_path.read_text()
    scenarios = []

    # Split by Scenario/Feature blocks
    # Gherkin files in BEWT may or may not have explicit "Scenario:" prefix
    current_scenario_name = file_path.stem
    then_clauses = []
    keyword = None  # the step keyword an And/But continues

    for line in text.split('\n'):
        stripped = line.strip()
        lower = stripped.lower()

        # Detect scenario name
        if lower.startswith('scenario:'):
            # Save previous scenario if it has Then clauses
            if then_clauses:
                scenarios.append(GherkinScenario(
                    feature_file=str(file_path),
                    scenario_name=current_scenario_name,
                    then_clauses=then_clauses,
                ))
                then_clauses = []
            current_scenario_name = stripped.split(':', 1)[1].strip()
            keyword = None

        elif lower.startswith('feature:'):
            if not current_scenario_name or current_scenario_name == file_path.stem:
                current_scenario_name = stripped.split(':', 1)[1].strip()

        elif lower.startswith(('given ', 'when ')):
            keyword = lower.split(' ', 1)[0]

        elif lower.startswith('then '):
            keyword = 'then'
            step = stripped[5:].strip()
            if _is_assertion_step(step):
                then_clauses.append(step)

        elif lower.startswith(('and ', 'but ')) and keyword == 'then':
            step = stripped.split(' ', 1)[1].strip()
            if _is_assertion_step(step):
                then_clauses.append(step)

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
