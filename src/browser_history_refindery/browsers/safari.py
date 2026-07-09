"""Read history from Safari's ``History.db``."""

from contextlib import closing
from datetime import datetime
from pathlib import Path

from browser_history_refindery.browsers.base import (
    BrowserProfile,
    VisitRecord,
    from_safari_s,
    to_safari_s,
)
from browser_history_refindery.browsers.snapshot import open_readonly

# Safari stores titles on visits, not items. The bare v.title column combined
# with MAX(v.visit_time) resolves to the newest visit's title -- a documented
# SQLite guarantee for bare columns alongside a lone MAX/MIN aggregate.
_QUERY = """
    SELECT i.url, v.title, COUNT(v.id), MIN(v.visit_time), MAX(v.visit_time)
    FROM history_visits AS v JOIN history_items AS i ON i.id = v.history_item
    WHERE v.visit_time > :since
    GROUP BY i.id
"""


def read_safari_history(
    db_path: Path,
    profile: BrowserProfile,
    *,
    since: datetime | None = None,
) -> list[VisitRecord]:
    """Read per-URL aggregated visits from Safari's ``History.db``."""
    since_s = to_safari_s(since) if since is not None else 0.0
    with closing(open_readonly(db_path)) as conn:
        rows = conn.execute(_QUERY, {"since": since_s}).fetchall()
    return [
        VisitRecord(
            url=url,
            title=title or None,
            visit_count=count,
            first_visit_at=from_safari_s(first),
            last_visit_at=from_safari_s(last),
            profile=profile,
        )
        for url, title, count, first, last in rows
    ]
