"""Golden-value tests for browser epoch conversions."""

from datetime import UTC, datetime

import pytest

from browser_history_refindery.browsers.base import (
    from_chromium_us,
    from_firefox_us,
    from_safari_s,
    to_chromium_us,
    to_firefox_us,
    to_safari_s,
)

MOMENT = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


# 2026-07-09T12:00:00Z as Unix seconds; the Windows/Chromium epoch offset is
# the well-known 11_644_473_600 s.
UNIX_S = 1_783_598_400


def test_chromium_golden():
    assert from_chromium_us((11_644_473_600 + UNIX_S) * 1_000_000) == MOMENT


def test_firefox_golden():
    assert from_firefox_us(UNIX_S * 1_000_000) == MOMENT


def test_safari_golden():
    assert from_safari_s(UNIX_S - 978_307_200) == MOMENT


@pytest.mark.parametrize(
    ("to_fn", "from_fn"),
    [
        (to_chromium_us, from_chromium_us),
        (to_firefox_us, from_firefox_us),
        (to_safari_s, from_safari_s),
    ],
)
def test_round_trip(to_fn, from_fn):
    assert from_fn(to_fn(MOMENT)) == MOMENT


def test_chromium_zero_is_epoch():
    assert from_chromium_us(0) == datetime(1601, 1, 1, tzinfo=UTC)
