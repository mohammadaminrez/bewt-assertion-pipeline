from src.parsing.gherkin_parser import parse_feature_file


def _then(tmp_path, text):
    path = tmp_path / "01_Sample.feature"
    path.write_text(text)
    (scenario,) = parse_feature_file(path)
    return scenario.then_clauses


def test_logout_teardown_after_the_check_is_not_an_assertion(tmp_path):
    # Kanboard / Claroline shape: the last block only logs out.
    clauses = _then(tmp_path, """Feature: Project management
Scenario: Adds a new category
\tGiven the user is on the login page (/login)
\t\tWhen the user enters "admin" in the "Username" field
\t\tAnd clicks the "Save" button
\tThen "New Category 2" is shown in the table

\tGiven the previous assertion passed
\tThen the user clicks on the "A" icon in the top-right corner of the screen
\tAnd clicks the "Logout" link
""")
    assert clauses == ['"New Category 2" is shown in the table']


def test_and_under_a_later_when_is_an_action(tmp_path):
    # MantisBT shape: a second block with new actions and a real second check.
    clauses = _then(tmp_path, """Feature: Projects
\tGiven the user is logged in
\t\tWhen the user clicks the "Delete Project" button
\tThen "Project001" is not shown on the page

\tGiven the previous assertion passed
\t\tWhen the user clicks the "Manage" link
\t\tAnd clicks the "Delete Project" button
\tThen "testSubProject1" is not shown on the page
\tAnd "testSubProject2" is not shown on the page
""")
    assert clauses == [
        '"Project001" is not shown on the page',
        '"testSubProject1" is not shown on the page',
        '"testSubProject2" is not shown on the page',
    ]


def test_single_then_with_and_checks_is_unchanged(tmp_path):
    clauses = _then(tmp_path, """Feature: Language
\tGiven the user is on the settings page
\t\tWhen the user selects "Italiano" in the "Language" dropdown select
\tThen "Italiano (Italia)" is shown as selected value in the "Language" dropdown select
\tAnd "English" is not shown on the page
""")
    assert clauses == [
        '"Italiano (Italia)" is shown as selected value in the "Language" dropdown select',
        '"English" is not shown on the page',
    ]


def test_spec_names_match_tests_with_app_prefix_or_test_suffix():
    from src.models import GherkinScenario
    from src.parsing.gherkin_parser import match_gherkin_to_test
    gmap = {"addcategory": GherkinScenario("AddCategory.feature", "s"),
            "addcoursetest": GherkinScenario("AddCourseTest.feature", "s"),
            "addasdrafttest": GherkinScenario("AddAsDraftTest.feature", "s")}
    # MantisBT: test has "Test", spec does not
    assert match_gherkin_to_test("AddCategoryTest", gmap, "mantisbt").feature_file == "AddCategory.feature"
    # Claroline: test has the app name in front
    assert match_gherkin_to_test("ClarolineAddCourseTest", gmap, "claroline").feature_file == "AddCourseTest.feature"
    # exact names still match; unknown names do not
    assert match_gherkin_to_test("AddAsDraftTest", gmap, "bludit").feature_file == "AddAsDraftTest.feature"
    assert match_gherkin_to_test("DeleteUserTest", gmap, "bludit") is None
