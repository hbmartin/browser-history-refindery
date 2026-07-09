"""Adaptive cool-off pacing for URL submissions."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from browser_history_refindery.config import PacingConfig


def _default_sleep(seconds: float) -> Awaitable[None]:
    return asyncio.sleep(seconds)


@dataclass
class AdaptivePacer:
    """Spaces submissions out, backing off on errors and server backlog.

    ``clock`` and ``sleep`` are injectable so tests can drive it
    deterministically.
    """

    config: PacingConfig
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = _default_sleep
    _interval: float = field(init=False)
    _backlog_slow: bool = field(init=False, default=False)
    _next_allowed: float = field(init=False)

    def __post_init__(self) -> None:
        self._interval = self.config.base_interval
        self._next_allowed = self.clock()

    @property
    def effective_interval(self) -> float:
        """Current spacing between submissions, in seconds."""
        multiplier = self.config.queue_slowdown_factor if self._backlog_slow else 1.0
        return self._interval * multiplier

    async def wait(self) -> None:
        """Sleep until the next submission slot, then reserve the one after."""
        now = self.clock()
        if (delay := self._next_allowed - now) > 0:
            await self.sleep(delay)
        self._next_allowed = max(now, self._next_allowed) + self.effective_interval

    def on_success(self) -> None:
        """Speed back up toward the floor after a successful submission."""
        self._interval = max(
            self.config.floor, self._interval * self.config.recovery_factor
        )

    def on_failure(self) -> None:
        """Back off exponentially after a timeout, 5xx, or connection error."""
        self._interval = min(
            self.config.ceiling, self._interval * self.config.backoff_factor
        )

    def on_backlog(self, depth: int) -> None:
        """Throttle while the server's pending-job backlog is above threshold."""
        self._backlog_slow = depth > self.config.queue_depth_threshold
