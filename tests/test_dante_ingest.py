import json
from pathlib import Path

import pytest

from src.ingest.dante import (
    PLACEHOLDER,
    crawled_variant_source,
    ingest_dante_suite,
    load_state_graph,
    parse_generated_suite,
    to_test_records,
)

SUITE_SOURCE = """package tests;

import java.util.concurrent.TimeUnit;
import java.util.List;

import org.junit.BeforeClass;
import org.junit.AfterClass;
import org.junit.Before;
import org.junit.Test;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import utils.DriverProvider;
import utils.Properties;

public class GeneratedTestSuiteFired {

\tprivate static WebDriver driver;

\t@BeforeClass
\tpublic static void oneTimeSetUp() throws Exception {
\t\tdriver = DriverProvider.getInstance().getDriver();
\t\tdriver.get(Properties.app_url);
\t}

\t@Test()
\tpublic void test0() throws Exception {
\t\tdriver.get("http://localhost:8080");
\t\tThread.sleep(250);
\t\tdriver.findElement(By.xpath("//A[@id = 'new-content']")).click();
\t\tThread.sleep(200);
\t\tdriver.findElement(By.id("title")).sendKeys("My first post");
\t\tdriver.findElement(By.xpath("//BUTTON[@id = 'save']")).click();
\t\tThread.sleep(200);
\t}

\t@Test()
\tpublic void test1() throws Exception {
\t\tdriver.get("http://localhost:8080");
\t\tThread.sleep(250);
\t\tdriver.findElement(By.xpath("//a[@id=\\"unknown\\"]")).click();
\t\tThread.sleep(200);
\t}
}
"""

RESULT_JSON = {
    "states": {
        "index": {"name": "index", "url": "http://localhost:8080/"},
        "state1": {"name": "state1", "url": "http://localhost:8080/new"},
        "state2": {"name": "state2", "url": "http://localhost:8080/saved"},
    },
    "edges": [
        {
            "from": "index",
            "to": "state1",
            "id": "xpath //A[@id = 'new-content']",
            "text": "New content",
            "element": "Element{tag=A, text=New content}",
            "eventType": "click",
        },
        {
            "from": "state1",
            "to": "state2",
            "id": "xpath //BUTTON[@id = 'save']",
            "text": "Save",
            "element": "Element{tag=BUTTON, text=Save}",
            "eventType": "click",
        },
    ],
}


@pytest.fixture
def crawl_dir(tmp_path: Path) -> Path:
    crawl = tmp_path / "crawl-with-inputs"
    (crawl / "doms").mkdir(parents=True)
    (crawl / "result.json").write_text(json.dumps(RESULT_JSON))
    for state in RESULT_JSON["states"]:
        (crawl / "doms" / f"{state}.html").write_text(f"<html><body>{state}</body></html>")
    return crawl


@pytest.fixture
def suite_path(tmp_path: Path) -> Path:
    path = tmp_path / "GeneratedTestSuiteFired.java"
    path.write_text(SUITE_SOURCE)
    return path


def test_parse_generated_suite_finds_all_methods(suite_path):
    methods = parse_generated_suite(suite_path)
    assert [name for name, _ in methods] == ["test0", "test1"]
    assert "new-content" in methods[0][1]


def test_load_state_graph_normalizes_edge_keys(crawl_dir):
    _, edges = load_state_graph(crawl_dir)
    assert ("index", "//a[@id='new-content']") in edges
    assert edges[("index", "//a[@id='new-content']")].target == "state1"


def test_ingest_writes_placeholdered_class_per_test(suite_path, crawl_dir, tmp_path):
    out = tmp_path / "out"
    tests = ingest_dante_suite("bludit", suite_path, crawl_dir, out)

    assert len(tests) == 2
    for t in tests:
        class_file = out / "tests" / f"{t.class_name}.java"
        assert class_file.exists()
        source = class_file.read_text()
        assert PLACEHOLDER in source
        assert f"public class {t.class_name}" in source
        # Original assertion-less body is preserved.
        assert "driver.get(" in source

    state_map = json.loads((out / "state_map.json").read_text())
    assert set(state_map) == {t.class_name for t in tests}


def test_ingest_maps_final_state_dom(suite_path, crawl_dir, tmp_path):
    tests = ingest_dante_suite("bludit", suite_path, crawl_dir, tmp_path / "out")

    t0 = next(t for t in tests if t.method_name == "test0")
    assert t0.final_state == "state2"
    assert t0.state_dom_path is not None
    assert Path(t0.state_dom_path).read_text() == "<html><body>state2</body></html>"

    # test1 clicks an element with no matching edge: stays anchored at index.
    t1 = next(t for t in tests if t.method_name == "test1")
    assert t1.final_state == "index"
    assert t1.state_dom_path is not None


def test_trace_comment_describes_actions(suite_path, crawl_dir, tmp_path):
    tests = ingest_dante_suite("bludit", suite_path, crawl_dir, tmp_path / "out")
    t0 = next(t for t in tests if t.method_name == "test0")
    assert 'clicks "New content"' in t0.trace_comment
    assert 'types "My first post" into title' in t0.trace_comment
    assert 'clicks "Save"' in t0.trace_comment
    assert "state2" in t0.trace_comment


def test_to_test_records_have_no_gold_assertions(suite_path, crawl_dir, tmp_path):
    tests = ingest_dante_suite("bludit", suite_path, crawl_dir, tmp_path / "out")
    records = to_test_records(tests, "3.13.1")
    assert all(r.assertions == [] for r in records)
    assert all(r.variant == "crawled" for r in records)
    assert records[0].version == "3.13.1"
    assert PLACEHOLDER in records[0].stripped_source


def test_crawled_variant_source_inserts_comment_above_placeholder(
    suite_path, crawl_dir, tmp_path
):
    tests = ingest_dante_suite("bludit", suite_path, crawl_dir, tmp_path / "out")
    records = to_test_records(tests, "3.13.1")
    t0, r0 = tests[0], records[0]

    with_comment = crawled_variant_source(r0, t0.trace_comment)
    comment_idx = with_comment.find("// The test")
    placeholder_idx = with_comment.find(PLACEHOLDER)
    assert 0 <= comment_idx < placeholder_idx

    # Without a comment the source is unchanged.
    assert crawled_variant_source(r0, None) == r0.stripped_source
