"""Tests for package metadata consistency."""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest

from browser_history_refindery import __version__


def test_package_versions_match() -> None:
    """Keep source, project, and installed distribution versions aligned."""
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    project_version = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))[
        "project"
    ]["version"]

    assert isinstance(project_version, str)
    assert project_version == __version__
    distribution_name = "browser-history-refindery"
    try:
        installed_version = distribution_version(distribution_name)
    except PackageNotFoundError:
        pytest.fail(
            f"The package {distribution_name!r} is not installed. "
            "Install the project with `uv sync` before running this test.",
            pytrace=False,
        )

    assert project_version == installed_version
