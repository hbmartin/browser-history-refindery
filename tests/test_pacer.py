"""Deterministic pacer math with an injected clock and sleep."""

import pytest

from browser_history_refindery.config import PacingConfig
from browser_history_refindery.pacer import AdaptivePacer


class Harness:
    def __init__(self, config: PacingConfig):
        self.now = 0.0
        self.sleeps: list[float] = []
        self.pacer = AdaptivePacer(
            config=config, clock=lambda: self.now, sleep=self.sleep
        )

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def harness():
    return Harness(PacingConfig(base_interval=1.0, floor=0.5, ceiling=8.0))


async def test_first_wait_is_immediate(harness):
    await harness.pacer.wait()
    assert harness.sleeps == []


async def test_second_wait_spaces_by_interval(harness):
    await harness.pacer.wait()
    await harness.pacer.wait()
    assert harness.sleeps == [1.0]


async def test_backoff_and_ceiling(harness):
    for _ in range(10):
        harness.pacer.on_failure()
    assert harness.pacer.effective_interval == 8.0


async def test_recovery_toward_floor(harness):
    for _ in range(200):
        harness.pacer.on_success()
    assert harness.pacer.effective_interval == 0.5


async def test_backlog_slowdown(harness):
    harness.pacer.on_backlog(depth=101)
    assert harness.pacer.effective_interval == 2.0
    harness.pacer.on_backlog(depth=5)
    assert harness.pacer.effective_interval == 1.0


async def test_elapsed_time_reduces_sleep(harness):
    await harness.pacer.wait()
    harness.now += 0.75  # caller spent time doing the request
    await harness.pacer.wait()
    assert harness.sleeps == [pytest.approx(0.25)]
