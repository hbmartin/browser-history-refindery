"""Browser history discovery and readers."""

from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import assert_never

from browser_history_refindery.browsers.base import (
    BrowserFamily,
    BrowserProfile,
    VisitRecord,
)
from browser_history_refindery.browsers.chromium import read_chromium_history
from browser_history_refindery.browsers.discovery import discover_all
from browser_history_refindery.browsers.firefox import read_firefox_history
from browser_history_refindery.browsers.safari import read_safari_history
from browser_history_refindery.browsers.snapshot import (
    FullDiskAccessError,
    history_snapshot,
    open_readonly,
)

__all__ = [
    "BrowserFamily",
    "BrowserProfile",
    "FullDiskAccessError",
    "VisitRecord",
    "count_urls",
    "discover_all",
    "read_profile",
]

_COUNT_QUERIES: dict[BrowserFamily, str] = {
    BrowserFamily.CHROMIUM: "SELECT COUNT(*) FROM urls WHERE hidden = 0",
    BrowserFamily.FIREFOX: "SELECT COUNT(*) FROM moz_places WHERE hidden = 0",
    BrowserFamily.SAFARI: "SELECT COUNT(*) FROM history_items",
}


def read_profile(
    profile: BrowserProfile, *, since: datetime | None = None
) -> list[VisitRecord]:
    """Read one profile's aggregated history from a temporary snapshot copy."""
    with history_snapshot(profile.history_path) as snapshot:
        return _read_family(snapshot, profile, since=since)


def _read_family(
    db_path: Path, profile: BrowserProfile, *, since: datetime | None
) -> list[VisitRecord]:
    match profile.family:
        case BrowserFamily.CHROMIUM:
            return read_chromium_history(db_path, profile, since=since)
        case BrowserFamily.FIREFOX:
            return read_firefox_history(db_path, profile, since=since)
        case BrowserFamily.SAFARI:
            return read_safari_history(db_path, profile, since=since)
        case _:
            assert_never(profile.family)


def count_urls(profile: BrowserProfile) -> int:
    """Count distinct history URLs in a profile (for the list-profiles view)."""
    with (
        history_snapshot(profile.history_path) as snapshot,
        closing(open_readonly(snapshot)) as conn,
    ):
        (count,) = conn.execute(_COUNT_QUERIES[profile.family]).fetchone()
    return int(count)
