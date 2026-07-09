"""Read history from Firefox ``places.sqlite`` databases."""

from contextlib import closing
from datetime import datetime
from pathlib import Path

from browser_history_refindery.browsers.base import (
    BrowserProfile,
    VisitRecord,
    from_firefox_us,
    to_firefox_us,
)
from browser_history_refindery.browsers.snapshot import open_readonly

_QUERY = """
    SELECT p.url, p.title, COUNT(h.id), MIN(h.visit_date), MAX(h.visit_date)
    FROM moz_historyvisits AS h JOIN moz_places AS p ON p.id = h.place_id
    WHERE p.hidden = 0 AND h.visit_date > :since
    GROUP BY p.id
"""


def read_firefox_history(
    db_path: Path,
    profile: BrowserProfile,
    *,
    since: datetime | None = None,
) -> list[VisitRecord]:
    """Read per-URL aggregated visits from a Firefox ``places.sqlite``."""
    since_us = to_firefox_us(since) if since is not None else 0
    with closing(open_readonly(db_path)) as conn:
        rows = conn.execute(_QUERY, {"since": since_us}).fetchall()
    return [
        VisitRecord(
            url=url,
            title=title or None,
            visit_count=count,
            first_visit_at=from_firefox_us(first),
            last_visit_at=from_firefox_us(last),
            profile=profile,
        )
        for url, title, count, first, last in rows
    ]
