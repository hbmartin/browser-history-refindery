"""Config loading, first-run template, and token resolution."""

import pytest
from pydantic import ValidationError

from browser_history_refindery.config import (
    REFINDERY_AUTH_TOKEN_ENV,
    AppConfig,
    MissingTokenError,
    ServerConfig,
    load_or_create,
)


def test_first_run_writes_template(tmp_path):
    path = tmp_path / "config.toml"
    config, created = load_or_create(path)
    assert created
    assert path.exists()
    assert config.pacing.base_interval == 1.0
    # The template itself must parse back to the defaults.
    reloaded, created_again = load_or_create(path)
    assert not created_again
    assert reloaded == config


def test_load_overrides(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[server]
base_url = "http://127.0.0.1:9999"
auth_token = "tok"

[pacing]
base_interval = 0.25

[exclusions]
banking = false
skip_domains = ["secret.example"]
"""
    )
    config = AppConfig.load(path)
    assert config.server.base_url == "http://127.0.0.1:9999"
    assert config.pacing.base_interval == 0.25
    assert config.exclusions.banking is False
    assert config.exclusions.skip_domains == ["secret.example"]


def test_typo_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[pacing]\nbase_intervall = 2.0\n")
    with pytest.raises(ValidationError):
        AppConfig.load(path)


def test_token_from_env(monkeypatch):
    monkeypatch.setenv(REFINDERY_AUTH_TOKEN_ENV, "env-token")
    assert ServerConfig().resolve_token() == "env-token"


def test_token_config_wins_over_env(monkeypatch):
    monkeypatch.setenv(REFINDERY_AUTH_TOKEN_ENV, "env-token")
    assert ServerConfig(auth_token="cfg").resolve_token() == "cfg"


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv(REFINDERY_AUTH_TOKEN_ENV, raising=False)
    with pytest.raises(MissingTokenError):
        ServerConfig().resolve_token()
