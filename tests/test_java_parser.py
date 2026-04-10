"""Tests for the Java test file parser."""

from src.parsing.java_parser import extract_assertions, strip_assertions, parse_class_name, parse_method_name
from src.models import AssertionType


SAMPLE_TEST_EQUALS = """package tests;

import static org.junit.Assert.assertEquals;
import org.junit.Test;
import po.LoginPage;
import po.ProjectSummaryPage;

public class AddNewProject extends BaseTest {

    @Test()
    public void addNewProject() {
        ProjectSummaryPage project = new LoginPage(driver)
                .loginToKanboard("admin", password)
                .newProject()
                .addNewProject("Test 2");

        assertEquals("Test 2", project.getTitle());
        assertEquals("This project is open", project.getStatus());
    }
}
"""

SAMPLE_TEST_TRUE = """package tests;

import static org.junit.Assert.assertTrue;
import org.junit.Test;
import po.AdminLoginPage;
import po.NewProductPage;

public class AddEmptyProductTest extends BaseTest {

    @Test
    public void testExpressCartAddEmptyProduct() throws Exception {
        goToAdminHome();
        NewProductPage product = new AdminLoginPage(driver)
                .setEmail("owner@test.com")
                .setPassword("test")
                .doLogin()
                .newProduct()
                .addProductError();

        assertTrue(product.productTitleHasError());
        assertTrue(product.productPriceHasError());
    }
}
"""


def test_extract_assertEquals():
    assertions = extract_assertions(SAMPLE_TEST_EQUALS)
    assert len(assertions) == 2
    assert assertions[0].assertion_type == AssertionType.ASSERT_EQUALS
    assert assertions[0].expected_value == '"Test 2"'
    assert assertions[0].actual_expression == 'project.getTitle()'
    assert assertions[1].expected_value == '"This project is open"'


def test_extract_assertTrue():
    assertions = extract_assertions(SAMPLE_TEST_TRUE)
    assert len(assertions) == 2
    assert assertions[0].assertion_type == AssertionType.ASSERT_TRUE
    assert assertions[1].assertion_type == AssertionType.ASSERT_TRUE


def test_strip_assertions():
    assertions = extract_assertions(SAMPLE_TEST_EQUALS)
    stripped = strip_assertions(SAMPLE_TEST_EQUALS, assertions)
    # Import line still has assertEquals, but assertion lines should be gone
    lines = [l.strip() for l in stripped.split('\n')]
    assert not any(l.startswith('assertEquals') for l in lines)
    assert "// TODO: Insert the missing assertion here" in stripped


def test_parse_class_name():
    assert parse_class_name(SAMPLE_TEST_EQUALS) == "AddNewProject"
    assert parse_class_name(SAMPLE_TEST_TRUE) == "AddEmptyProductTest"


def test_parse_method_name():
    assert parse_method_name(SAMPLE_TEST_EQUALS) == "addNewProject"
    assert parse_method_name(SAMPLE_TEST_TRUE) == "testExpressCartAddEmptyProduct"


def test_strip_preserves_structure():
    assertions = extract_assertions(SAMPLE_TEST_TRUE)
    stripped = strip_assertions(SAMPLE_TEST_TRUE, assertions)
    # Should still have the class, method, and action code
    assert "class AddEmptyProductTest" in stripped
    assert "testExpressCartAddEmptyProduct" in stripped
    assert "addProductError" in stripped
    lines = [l.strip() for l in stripped.split('\n')]
    assert not any(l.startswith('assertTrue') for l in lines)


if __name__ == "__main__":
    test_extract_assertEquals()
    print("✓ test_extract_assertEquals")
    test_extract_assertTrue()
    print("✓ test_extract_assertTrue")
    test_strip_assertions()
    print("✓ test_strip_assertions")
    test_parse_class_name()
    print("✓ test_parse_class_name")
    test_parse_method_name()
    print("✓ test_parse_method_name")
    test_strip_preserves_structure()
    print("✓ test_strip_preserves_structure")
    print("\nAll tests passed!")
