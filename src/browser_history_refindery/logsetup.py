"""Loguru-based event logging.

All package modules import :data:`logger` from here rather than from ``loguru``
directly, so that importing any of them triggers the module-load ``disable``
below. That keeps the event log silent by default (no stderr spam in tests or
library use); :func:`configure_logging` opts a CLI process back in and attaches
the configured file sink.
"""

from loguru import logger

from browser_history_refindery.config import LoggingConfig

__all__ = ["configure_logging", "logger"]

_PACKAGE = "browser_history_refindery"

# Silent until a CLI entry point calls configure_logging().
logger.disable(_PACKAGE)


def configure_logging(config: LoggingConfig) -> None:
    """Point the event log at the configured file sink (or leave it disabled)."""
    logger.remove()
    if not config.enabled:
        logger.disable(_PACKAGE)
        return
    logger.enable(_PACKAGE)
    logger.add(
        config.path,
        level=config.level,
        rotation=config.rotation,
        retention=config.retention,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )
