from __future__ import annotations

"""Automated app setup: run Installer.java to configure freshly deployed apps."""

import shutil
import subprocess
from pathlib import Path

from ..config import Config


def find_installer_class(config: Config, app: str) -> str | None:
    """Find the fully qualified Installer class name for an app."""
    project_path = config.get_app_project_path(app)
    java_root = project_path / "src" / "test" / "java"

    if not java_root.exists():
        # Some apps have Installer in src/main/java
        java_root = project_path / "src" / "main" / "java"

    for installer in java_root.rglob("Installer.java"):
        # Read package declaration
        for line in installer.read_text().splitlines():
            if line.strip().startswith("package "):
                pkg = line.strip().replace("package ", "").replace(";", "").strip()
                return f"{pkg}.Installer"
    return None


def run_installer(
    config: Config,
    app: str,
    on_progress: callable | None = None,
) -> bool:
    """Run the Installer.java test to set up a freshly deployed app.

    Returns True if the installer completed (even with warnings).
    """
    def _log(msg: str):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    if not shutil.which("mvn"):
        _log("  Error: Maven (mvn) not found")
        return False

    installer_class = find_installer_class(config, app)
    if not installer_class:
        _log(f"  No Installer.java found for {app} — app may not need setup")
        return True

    project_path = config.get_app_project_path(app)
    _log(f"  Running installer: {installer_class}")

    process = subprocess.Popen(
        ["mvn", "test", f"-Dtest={installer_class}", "-pl", "."],
        cwd=str(project_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    for line in process.stdout:
        line = line.rstrip()
        if "BUILD SUCCESS" in line:
            _log("  Installer completed successfully")
        elif "BUILD FAILURE" in line:
            _log("  Installer failed")
        elif "Setup complete" in line or "Installation complete" in line:
            _log(f"  {line.strip()}")

    process.wait()

    # Post-install steps (app-specific)
    _post_install(config, app, on_progress=_log)

    return process.returncode == 0


def _post_install(config: Config, app: str, on_progress: callable | None = None) -> None:
    """App-specific post-install steps."""
    def _log(msg: str):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    if app == "mediawiki":
        # MediaWiki needs LocalSettings.php copied into the container
        # The installer generates it, but headless Chrome can't download files.
        # Use the copy from another variant in the BEWT repo.
        local_settings = None
        for variant in ["no_pageobjects", "explicit_wait", "full_xpath"]:
            candidate = (
                config.bewt_repo_path / app / variant
                / f"{app}-{config.apps[app]['versions'][0]}" / "LocalSettings.php"
            )
            if candidate.exists():
                local_settings = candidate
                break

        if local_settings:
            _log("  Copying LocalSettings.php into MediaWiki container...")
            # Find the container name
            result = subprocess.run(
                ["docker", "ps", "--filter", "ancestor=mediawiki:1.40.0", "--format", "{{.Names}}"],
                capture_output=True, text=True,
            )
            container = result.stdout.strip().split("\n")[0] if result.stdout.strip() else None

            if container:
                subprocess.run(
                    ["docker", "cp", str(local_settings), f"{container}:/var/www/html/LocalSettings.php"],
                    capture_output=True, text=True,
                )
                _log("  LocalSettings.php copied. Restarting container...")
                subprocess.run(["docker", "restart", container], capture_output=True, text=True)
                _log("  MediaWiki setup complete")
            else:
                _log("  Warning: Could not find MediaWiki container to copy LocalSettings.php")
        else:
            _log("  Warning: No LocalSettings.php found in BEWT repo variants")
