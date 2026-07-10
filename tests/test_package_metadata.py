"""Tests for package metadata consistency."""

import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

from browser_history_refindery import __version__


def test_package_versions_match() -> None:
    """Keep source, project, and installed distribution versions aligned."""
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    project_version = tomllib.loads(pyproject_path.read_text())["project"]["version"]

    assert isinstance(project_version, str)
    assert project_version == __version__
    assert project_version == distribution_version("browser-history-refindery")
