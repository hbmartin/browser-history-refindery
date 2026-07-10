"""Mutable run statistics shared by the pipeline tasks and the live UI.

All mutation happens on the single event-loop thread, so no locking is
needed anywhere in this module.
"""

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class ProfileStats:
    """Per-profile counters shown in the dashboard."""

    label: str
    urls_read: int = 0
    queued_for_submit: int = 0
    submitted: int = 0
    done: bool = False


@dataclass(slots=True)
class RunStats:
    """Counters and recent events for one import run."""

    started_at: datetime = field(default_factory=_now)
    # Read/plan phase (streamed per profile).
    profiles_total: int = 0
    profiles_read: int = 0
    urls_read_total: int = 0
    unique_urls: int = 0
    reading_finished: bool = False
    # Submit phase.
    total_to_submit: int = 0
    submitted: int = 0
    accepted: int = 0
    revisits: int = 0
    blacklisted: int = 0
    rejected: int = 0
    skipped: int = 0
    already_submitted: int = 0
    previously_rejected: int = 0
    errors: int = 0
    retries: int = 0
    indexed: int = 0
    dead: int = 0
    current_interval: float = 0.0
    queue_depth: int = 0
    server_backlog: int | None = None
    submitter_finished: bool = False
    per_profile: dict[str, ProfileStats] = field(default_factory=dict)
    skip_reasons: Counter[str] = field(default_factory=Counter)
    events: deque[str] = field(default_factory=lambda: deque(maxlen=15))

    @property
    def processed(self) -> int:
        """URLs fully handled by the submitter (any outcome, or given up)."""
        return self.submitted + self.errors

    @property
    def skipped_locally(self) -> int:
        """URLs never submitted because of local rules, dedup, or prior rejection."""
        return self.skipped + self.already_submitted + self.previously_rejected

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since the run began."""
        return max(0.0, (_now() - self.started_at).total_seconds())

    @property
    def throughput(self) -> float:
        """Submitted URLs per second so far (0.0 before anything is processed)."""
        return self.processed / elapsed if (elapsed := self.elapsed_seconds) else 0.0

    def add_event(self, message: str) -> None:
        """Append a timestamped line to the recent-events panel."""
        stamp = _now().strftime("%H:%M:%S")
        self.events.append(f"[{stamp}] {message}")
