"""Mutable run statistics shared by the pipeline tasks and the live UI.

All mutation happens on the single event-loop thread, so no locking is
needed anywhere in this module.
"""

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class ProfileStats:
    """Per-profile counters shown in the dashboard."""

    label: str
    urls_read: int = 0
    queued_for_submit: int = 0
    submitted: int = 0


@dataclass(slots=True)
class RunStats:
    """Counters and recent events for one import run."""

    total_to_submit: int = 0
    submitted: int = 0
    accepted: int = 0
    revisits: int = 0
    blacklisted: int = 0
    rejected: int = 0
    skipped: int = 0
    already_submitted: int = 0
    errors: int = 0
    indexed: int = 0
    dead: int = 0
    current_interval: float = 0.0
    server_backlog: int | None = None
    submitter_finished: bool = False
    per_profile: dict[str, ProfileStats] = field(default_factory=dict)
    skip_reasons: Counter[str] = field(default_factory=Counter)
    events: deque[str] = field(default_factory=lambda: deque(maxlen=12))

    @property
    def processed(self) -> int:
        """URLs fully handled by the submitter (any outcome, or given up)."""
        return self.submitted + self.errors

    def add_event(self, message: str) -> None:
        """Append a timestamped line to the recent-events panel."""
        stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
        self.events.append(f"[{stamp}] {message}")
