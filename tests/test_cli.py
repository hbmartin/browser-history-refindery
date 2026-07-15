"""CLI smoke tests via Typer's CliRunner."""

from pathlib import Path

from typer.testing import CliRunner

from browser_history_refindery import __version__
from browser_history_refindery.browsers import discovery
from browser_history_refindery.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("import", "list-profiles", "status-sweep", "forget", "blacklist"):
        assert command in result.output


def test_list_profiles(fake_home, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(discovery.sys, "platform", "darwin")
    result = runner.invoke(app, ["list-profiles"])
    assert result.exit_code == 0
    assert "Harold" in result.output
    assert "Safari" in result.output


def test_import_dry_run(fake_home, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(discovery.sys, "platform", "darwin")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[server]
auth_token = "tok"

[state]
db_path = "{tmp_path / "state.sqlite3"}"
"""
    )
    result = runner.invoke(
        app, ["import", "--dry-run", "--all", "--config", str(config_path)]
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "eligible pages" in result.output
    assert "Incremental resource estimate" in result.output


def test_import_db_requires_browser(tmp_path):
    db = tmp_path / "History"
    db.write_bytes(b"x")
    result = runner.invoke(app, ["import", "--db", str(db)])
    assert result.exit_code == 2
