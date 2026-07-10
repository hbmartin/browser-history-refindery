"""Shared browser history types and epoch conversions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
SAFARI_EPOCH_OFFSET_S = 978_307_200  # 2001-01-01T00:00:00Z as Unix seconds


class BrowserFamily(StrEnum):
    """History database schema family."""

    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    SAFARI = "safari"


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """One selectable browser profile backed by a history database."""

    browser_id: str
    browser_label: str
    profile_dir: str
    profile_name: str
    history_path: Path
    family: BrowserFamily

    @property
    def key(self) -> str:
        """Stable identifier used for per-profile stats."""
        return f"{self.browser_id}:{self.profile_dir}"

    @property
    def watermark_key(self) -> tuple[str, str]:
        """Persistent identity used for incremental-import watermarks."""
        return self.browser_id, str(self.history_path.resolve())

    @property
    def display(self) -> str:
        """Human-readable label for selection lists and tables."""
        return f"{self.browser_label} — {self.profile_name}"


@dataclass(frozen=True, slots=True)
class VisitRecord:
    """Aggregated visit data for one URL within one profile."""

    url: str
    title: str | None
    visit_count: int
    first_visit_at: datetime
    last_visit_at: datetime
    profile: BrowserProfile


def from_chromium_us(value: int) -> datetime:
    """Convert Chromium microseconds since 1601-01-01 UTC to a datetime."""
    return CHROMIUM_EPOCH + timedelta(microseconds=value)


def to_chromium_us(moment: datetime) -> int:
    """Convert an aware datetime to Chromium microseconds since 1601-01-01."""
    return round((moment - CHROMIUM_EPOCH).total_seconds() * 1_000_000)


def from_firefox_us(value: int) -> datetime:
    """Convert Firefox microseconds since the Unix epoch to a datetime."""
    return datetime.fromtimestamp(value / 1_000_000, tz=UTC)


def to_firefox_us(moment: datetime) -> int:
    """Convert an aware datetime to Firefox microseconds since the Unix epoch."""
    return round(moment.timestamp() * 1_000_000)


def from_safari_s(value: float) -> datetime:
    """Convert Safari seconds since 2001-01-01 UTC to a datetime."""
    return datetime.fromtimestamp(value + SAFARI_EPOCH_OFFSET_S, tz=UTC)


def to_safari_s(moment: datetime) -> float:
    """Convert an aware datetime to Safari seconds since 2001-01-01 UTC."""
    return moment.timestamp() - SAFARI_EPOCH_OFFSET_S
