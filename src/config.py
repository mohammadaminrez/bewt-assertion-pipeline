from __future__ import annotations

"""Configuration loader for the pipeline."""

from pathlib import Path
import yaml


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class Config:
    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        self.config_dir = config_dir
        self._apps = load_yaml(config_dir / "apps.yaml")
        self._models = load_yaml(config_dir / "models.yaml")
        self._metrics = load_yaml(config_dir / "metrics.yaml")

    @property
    def apps(self) -> dict:
        return self._apps["apps"]

    @property
    def bewt_repo_path(self) -> Path:
        return Path(self._apps["bewt_repo_path"]).resolve()

    @property
    def output_dir(self) -> Path:
        return Path(self._apps["output_dir"]).resolve()

    @property
    def models(self) -> dict:
        return self._models["models"]

    @property
    def default_model(self) -> str:
        return self._models["default_model"]

    @property
    def retry_attempts(self) -> int:
        return self._models["retry_attempts"]

    @property
    def cache_responses(self) -> bool:
        return self._models["cache_responses"]

    @property
    def error_categories(self) -> list[str]:
        return self._metrics["evaluation"]["error_categories"]

    @property
    def significance_level(self) -> float:
        return self._metrics["evaluation"]["significance_level"]

    def get_app_test_path(self, app: str, variant: str | None = None, version: str | None = None) -> Path:
        """Get the path to test files for a given app."""
        app_config = self.apps[app]
        variant = variant or app_config["variant"]
        version = version or app_config["versions"][0]
        base = self.bewt_repo_path / app / variant / f"{app}-{version}" / "src" / "test" / "java"
        # Apps use different package names: tests, test, base, or app name
        for pkg in ["tests", "test", "base", app]:
            pkg_dir = base / pkg
            if pkg_dir.exists():
                return pkg_dir
        return base / "tests"  # default

    def get_app_po_path(self, app: str, variant: str | None = None, version: str | None = None) -> Path:
        """Get the path to page object files for a given app."""
        app_config = self.apps[app]
        variant = variant or app_config["variant"]
        version = version or app_config["versions"][0]
        return self.bewt_repo_path / app / variant / f"{app}-{version}" / "src" / "main" / "java" / "po"

    def get_app_project_path(self, app: str, variant: str | None = None, version: str | None = None) -> Path:
        """Get the Maven project root for a given app."""
        app_config = self.apps[app]
        variant = variant or app_config["variant"]
        version = version or app_config["versions"][0]
        return self.bewt_repo_path / app / variant / f"{app}-{version}"

    def get_gherkin_path(self, app: str, version: str | None = None) -> Path:
        """Get the path to gherkin feature files."""
        app_config = self.apps[app]
        version = version or app_config["versions"][0]
        gherkin_dir = self.bewt_repo_path / app / "gherkin"
        # Some apps have version subdirectories, some don't
        versioned = gherkin_dir / f"{app}-{version}"
        if versioned.exists():
            return versioned
        return gherkin_dir
