from __future__ import annotations

"""Manage Docker containers for web applications."""

import subprocess
import time
from pathlib import Path

from ..config import Config


class DockerManager:
    def __init__(self, config: Config):
        self.config = config

    def deploy_app(self, app: str, version: str | None = None) -> bool:
        """Deploy a web application using Docker Compose."""
        compose_path = self._find_compose_file(app, version)
        if not compose_path:
            print(f"Warning: No docker-compose file found for {app}. Assuming app is already running.")
            return True

        print(f"Deploying {app}...")
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "up", "-d"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"Error deploying {app}: {result.stderr}")
            return False

        # Wait for app to be ready
        base_url = self.config.apps[app]["base_url"]
        return self._wait_for_app(base_url)

    def destroy_app(self, app: str, version: str | None = None) -> bool:
        """Destroy a running application."""
        compose_path = self._find_compose_file(app, version)
        if not compose_path:
            return True

        print(f"Destroying {app}...")
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "down", "-v"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0

    def reset_app(self, app: str, version: str | None = None) -> bool:
        """Destroy and redeploy an application."""
        self.destroy_app(app, version)
        time.sleep(2)
        return self.deploy_app(app, version)

    def _find_compose_file(self, app: str, version: str | None = None) -> Path | None:
        """Find the docker-compose file for an app."""
        # Check config first
        app_config = self.config.apps[app]
        if app_config.get("docker_compose"):
            return Path(app_config["docker_compose"])

        # Search in the BEWT repo
        version = version or app_config["versions"][0]
        candidates = [
            self.config.bewt_repo_path / app / "docker-compose.yml",
            self.config.bewt_repo_path / app / "docker-compose.yaml",
            self.config.bewt_repo_path / app / f"docker-compose-{version}.yml",
            self.config.bewt_repo_path / app / "baseline" / f"{app}-{version}" / "docker-compose.yml",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _wait_for_app(self, url: str, timeout: int = 60, interval: int = 3) -> bool:
        """Wait until the app responds to HTTP requests."""
        import urllib.request
        import urllib.error

        start = time.time()
        while time.time() - start < timeout:
            try:
                urllib.request.urlopen(url, timeout=5)
                print(f"  App ready at {url}")
                return True
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(interval)

        print(f"  Timeout waiting for app at {url}")
        return False
