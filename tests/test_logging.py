"""Loguru event-log configuration and its integration with the pipeline."""

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console

from browser_history_refindery.browsers import BrowserFamily
from browser_history_refindery.config import AppConfig, LoggingConfig
from browser_history_refindery.logsetup import configure_logging, logger
from browser_history_refindery.pipeline import run_import
from tests.conftest import T1, make_chromium_db, profile_for


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Leave the global loguru logger silent after every test."""
    yield
    logger.remove()
    logger.disable("browser_history_refindery")


def _dry_run_config(tmp_path: Path, log_config: LoggingConfig) -> AppConfig:
    return AppConfig.model_validate(
        {
            "server": {"auth_token": "tok"},
            "state": {"db_path": str(tmp_path / "state.sqlite3")},
            "logging": log_config.model_dump(mode="json"),
        }
    )


async def _run_dry(tmp_path: Path) -> None:
    db = tmp_path / "History"
    make_chromium_db(db, [("https://logged.example/", "Logged", [T1], 0)])
    await run_import(
        config=AppConfig.model_validate(
            {
                "server": {"auth_token": "tok"},
                "state": {"db_path": str(tmp_path / "state.sqlite3")},
            }
        ),
        profiles=[profile_for(db, BrowserFamily.CHROMIUM)],
        console=Console(file=io.StringIO(), force_terminal=False),
        dry_run=True,
    )


async def test_enabled_writes_events(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    configure_logging(LoggingConfig(enabled=True, path=log_path, level="DEBUG"))
    await _run_dry(tmp_path)
    logger.remove()  # flush the enqueued sink

    assert log_path.exists()
    contents = log_path.read_text(encoding="utf-8")
    assert "read 1 urls" in contents


async def test_disabled_writes_nothing(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    configure_logging(LoggingConfig(enabled=False, path=log_path, level="DEBUG"))
    await _run_dry(tmp_path)
    logger.remove()

    assert not log_path.exists()


async def test_level_filters_below_threshold(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    # Only WARNING+ should be written; the pipeline's read line is DEBUG.
    configure_logging(LoggingConfig(enabled=True, path=log_path, level="WARNING"))
    await _run_dry(tmp_path)
    logger.remove()

    contents = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert "read 1 urls" not in contents


def test_configure_is_idempotent(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    config = LoggingConfig(enabled=True, path=log_path, level="INFO")
    configure_logging(config)
    configure_logging(config)  # must not stack duplicate sinks
    loguru_core = cast("Any", logger)._core  # noqa: SLF001 - inspecting loguru state
    assert len(loguru_core.handlers) == 1


def test_config_uses_logging_section(tmp_path: Path) -> None:
    config = _dry_run_config(
        tmp_path, LoggingConfig(enabled=False, level="ERROR", path=Path("x.log"))
    )
    assert config.logging.enabled is False
    assert config.logging.level == "ERROR"
