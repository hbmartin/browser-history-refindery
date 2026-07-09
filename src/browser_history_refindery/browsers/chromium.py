"""Read history from Chromium-family browsers (Chrome, Comet, Dia, Arc, ...)."""

from contextlib import closing
from datetime import datetime
from pathlib import Path

from browser_history_refindery.browsers.base import (
    BrowserProfile,
    VisitRecord,
    from_chromium_us,
    to_chromium_us,
)
from browser_history_refindery.browsers.snapshot import open_readonly

# visit_time of 0 means "unknown"; the strict > bound drops those rows.
_QUERY = """
    SELECT u.url, u.title, COUNT(v.id), MIN(v.visit_time), MAX(v.visit_time)
    FROM visits AS v JOIN urls AS u ON u.id = v.url
    WHERE u.hidden = 0 AND v.visit_time > :since
    GROUP BY u.id
"""


def read_chromium_history(
    db_path: Path,
    profile: BrowserProfile,
    *,
    since: datetime | None = None,
) -> list[VisitRecord]:
    """Read per-URL aggregated visits from a Chromium ``History`` database."""
    since_us = to_chromium_us(since) if since is not None else 0
    with closing(open_readonly(db_path)) as conn:
        rows = conn.execute(_QUERY, {"since": since_us}).fetchall()
    return [
        VisitRecord(
            url=url,
            title=title or None,
            visit_count=count,
            first_visit_at=from_chromium_us(first),
            last_visit_at=from_chromium_us(last),
            profile=profile,
        )
        for url, title, count, first, last in rows
    ]
