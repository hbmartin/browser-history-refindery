"""Config loading, first-run template, and token resolution."""

import pytest
from pydantic import ValidationError

from browser_history_refindery.config import (
    REFINDERY_AUTH_TOKEN_ENV,
    AppConfig,
    ConfigError,
    ExclusionsConfig,
    LoggingConfig,
    MissingTokenError,
    PacingConfig,
    PollerConfig,
    ServerConfig,
    SubmitConfig,
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


@pytest.mark.parametrize("bad_url", ["", "ftp://host", "127.0.0.1:8000", "not a url"])
def test_base_url_must_be_http(bad_url):
    with pytest.raises(ValidationError):
        ServerConfig(base_url=bad_url)


def test_base_url_trailing_slash_stripped():
    assert ServerConfig(base_url="http://host:8000/").base_url == "http://host:8000"


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_server_timeouts_must_be_positive(timeout):
    with pytest.raises(ValidationError):
        ServerConfig(request_timeout=timeout)


def test_pacing_floor_above_ceiling_rejected():
    with pytest.raises(ValidationError):
        PacingConfig(floor=10.0, ceiling=1.0)


def test_pacing_base_interval_above_ceiling_rejected():
    with pytest.raises(ValidationError):
        PacingConfig(base_interval=100.0, ceiling=60.0)


def test_pacing_base_below_floor_is_allowed():
    # The pacer clamps up to the floor on the first success, so this is valid.
    assert PacingConfig(base_interval=0.1, floor=0.5).base_interval == 0.1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recovery_factor": 1.5},
        {"recovery_factor": 0.0},
        {"backoff_factor": 0.5},
        {"max_attempts": 0},
        {"queue_slowdown_factor": 0.5},
    ],
)
def test_pacing_out_of_range_rejected(kwargs):
    with pytest.raises(ValidationError):
        PacingConfig(**kwargs)


@pytest.mark.parametrize("batch_size", [0, -3, 501])
def test_poller_batch_size_out_of_range_rejected(batch_size):
    with pytest.raises(ValidationError):
        PollerConfig(batch_size=batch_size)


@pytest.mark.parametrize("batch_size", [0, -3, 101])
def test_submit_batch_size_out_of_range_rejected(batch_size):
    with pytest.raises(ValidationError):
        SubmitConfig(batch_size=batch_size)


def test_submit_batch_size_defaults():
    assert SubmitConfig().batch_size == 50
    assert AppConfig().submit.batch_size == 50


@pytest.mark.parametrize(
    "domain", ["https://example.com", "example.com/path", "has space", "", "-bad-"]
)
def test_skip_domains_reject_non_bare_domains(domain):
    with pytest.raises(ValidationError):
        ExclusionsConfig(skip_domains=[domain])


def test_skip_domains_normalized_and_wildcard_allowed():
    config = ExclusionsConfig(skip_domains=["Example.COM", "*.internal.corp"])
    assert config.skip_domains == ["example.com", "*.internal.corp"]


def test_skip_patterns_reject_blank():
    with pytest.raises(ValidationError):
        ExclusionsConfig(skip_patterns=["  "])


def test_logging_level_normalized():
    assert LoggingConfig(level="debug").level == "DEBUG"


def test_logging_level_rejects_unknown():
    with pytest.raises(ValidationError):
        LoggingConfig(level="verbose")


def test_load_or_create_raises_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[server]\nbase_url = "ftp://nope"\n')
    with pytest.raises(ConfigError) as excinfo:
        load_or_create(path)
    message = str(excinfo.value)
    assert "server.base_url" in message
    assert str(path) in message


def test_default_template_passes_validation(tmp_path):
    path = tmp_path / "config.toml"
    _, created = load_or_create(path)
    assert created
    # Re-loading the written template must validate cleanly (no ConfigError).
    reloaded, created_again = load_or_create(path)
    assert not created_again
    assert reloaded == AppConfig()
