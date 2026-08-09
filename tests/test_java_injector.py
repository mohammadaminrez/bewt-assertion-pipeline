from __future__ import annotations

from src.execution.java_injector import _ensure_imports, prepare_project_copy
from src.models import TestRecord

SOURCE = """package base;

import org.junit.Test;

public class T {
    @Test
    public void t() {
        // TODO: Insert the missing assertion here
    }
}
"""


def test_assert_null_gets_its_import():
    out = _ensure_imports(SOURCE, "assertNull(page.getError());")
    assert "import static org.junit.Assert.assertNull;" in out


def test_assert_not_null_does_not_pull_in_assert_null():
    """'assertNotNull' must not be mistaken for 'assertNull'."""
    out = _ensure_imports(SOURCE, "assertNotNull(page.getError());")
    assert "import static org.junit.Assert.assertNotNull;" in out
    assert "import static org.junit.Assert.assertNull;" not in out


def test_existing_import_is_not_duplicated():
    source = SOURCE.replace(
        "import org.junit.Test;",
        "import static org.junit.Assert.assertEquals;\nimport org.junit.Test;",
    )
    out = _ensure_imports(source, 'assertEquals("a", page.value());')
    assert out.count("import static org.junit.Assert.assertEquals;") == 1


class _Config:
    def __init__(self, project: object, out: object):
        self._project = project
        self.output_dir = out

    def get_app_project_path(self, app):
        return self._project


def test_prepare_project_copy_leaves_stale_build_output_behind(tmp_path):
    """A previous run's Surefire reports must not reach the work dir."""
    project = tmp_path / "project"
    (project / "src" / "test" / "java" / "base").mkdir(parents=True)
    (project / "src" / "test" / "java" / "base" / "T.java").write_text(SOURCE)
    stale = project / "target" / "surefire-reports"
    stale.mkdir(parents=True)
    (stale / "TEST-base.T.xml").write_text("<testsuite/>")

    record = TestRecord(
        app="mantisbt", variant="baseline", version="1.0",
        file_path=str(project / "src" / "test" / "java" / "base" / "T.java"),
        class_name="T", method_name="t",
    )

    work_dir = prepare_project_copy(
        _Config(project, tmp_path / "out"), "mantisbt", record,
        'assertEquals("a", page.value());', SOURCE, "A", "gpt-4o",
    )

    assert not (work_dir / "target").exists()
    injected = (work_dir / "src" / "test" / "java" / "base" / "T.java").read_text()
    assert 'assertEquals("a", page.value());' in injected
    assert "TODO" not in injected
