"""Local progress-tracking SQLite database."""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from browser_history_refindery.browsers.base import BrowserProfile
from browser_history_refindery.filters import SkipReason

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    interrupted INTEGER NOT NULL DEFAULT 0,
    urls_seen   INTEGER NOT NULL DEFAULT 0,
    submitted   INTEGER NOT NULL DEFAULT 0,
    revisits    INTEGER NOT NULL DEFAULT 0,
    blacklisted INTEGER NOT NULL DEFAULT 0,
    rejected    INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS submissions (
    url               TEXT PRIMARY KEY,
    page_id           TEXT,
    outcome           TEXT NOT NULL,
    server_status     TEXT,
    last_error        TEXT,
    run_id            INTEGER NOT NULL REFERENCES runs(id),
    submitted_at      TEXT NOT NULL,
    last_visit_at     TEXT,
    status_checked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_submissions_page_id ON submissions(page_id);

CREATE INDEX IF NOT EXISTS idx_submissions_nonterminal ON submissions(server_status)
    WHERE page_id IS NOT NULL
      AND (server_status IS NULL OR server_status NOT IN ('indexed', 'dead'));

CREATE TABLE IF NOT EXISTS skips (
    url        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    rule       TEXT NOT NULL,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    skipped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_watermarks (
    browser_id    TEXT NOT NULL,
    history_path  TEXT NOT NULL,
    profile_name  TEXT NOT NULL,
    last_visit_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (browser_id, history_path)
);
"""


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class StateSchemaTooNewError(RuntimeError):
    """The state database was written by a newer version of this tool."""

    def __init__(self, version: int) -> None:
        super().__init__(
            f"state database schema is v{version} but this build supports up to "
            f"v{SCHEMA_VERSION}; upgrade refindery-import or point state.db_path "
            "at a different file"
        )


class StateStore:
    """Async wrapper around the local progress-tracking database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            msg = "StateStore is not open"
            raise RuntimeError(msg)
        return self._conn

    async def open(self) -> None:
        """Open the database, creating the schema on first use."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        cursor = await conn.execute("PRAGMA user_version")
        version = int(row[0]) if (row := await cursor.fetchone()) else 0
        if version > SCHEMA_VERSION:
            await conn.close()
            raise StateSchemaTooNewError(version)
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.executescript(_SCHEMA)
        cursor = await conn.execute("PRAGMA table_info(submissions)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        if "last_visit_at" not in columns:
            await conn.execute("ALTER TABLE submissions ADD COLUMN last_visit_at TEXT")
            await conn.execute(
                "UPDATE submissions SET last_visit_at = submitted_at "
                "WHERE last_visit_at IS NULL"
            )
        cursor = await conn.execute("PRAGMA table_info(runs)")
        run_columns = {str(row[1]) for row in await cursor.fetchall()}
        if "rejected" not in run_columns:
            await conn.execute(
                "ALTER TABLE runs ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0"
            )
        await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        """Commit and close the connection."""
        if self._conn is not None:
            await self._conn.commit()
            await self._conn.close()
            self._conn = None

    async def begin_run(self) -> int:
        """Insert a new run row and return its id."""
        cursor = await self._db.execute(
            "INSERT INTO runs (started_at) VALUES (?)", (_now_iso(),)
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def finish_run(
        self,
        run_id: int,
        *,
        interrupted: bool,
        urls_seen: int,
        submitted: int,
        revisits: int,
        blacklisted: int,
        rejected: int,
        skipped: int,
        errors: int,
    ) -> None:
        """Record final counters for a run."""
        await self._db.execute(
            """
            UPDATE runs SET finished_at = ?, interrupted = ?, urls_seen = ?,
                submitted = ?, revisits = ?, blacklisted = ?, rejected = ?,
                skipped = ?, errors = ?
            WHERE id = ?
            """,
            (
                _now_iso(),
                int(interrupted),
                urls_seen,
                submitted,
                revisits,
                blacklisted,
                rejected,
                skipped,
                errors,
                run_id,
            ),
        )
        await self._db.commit()

    async def load_submission_visit_times(self) -> dict[str, datetime]:
        """Map handled URLs to the newest browser visit represented by each POST."""
        cursor = await self._db.execute(
            "SELECT url, COALESCE(last_visit_at, submitted_at) FROM submissions"
        )
        return {
            row[0]: datetime.fromisoformat(row[1]) for row in await cursor.fetchall()
        }

    async def load_rejected_urls(self) -> set[str]:
        """Return permanently rejected URLs that must never be retried automatically."""
        cursor = await self._db.execute(
            "SELECT url FROM submissions WHERE outcome = 'rejected'"
        )
        return {str(row[0]) for row in await cursor.fetchall()}

    async def record_submission(
        self,
        *,
        url: str,
        outcome: str,
        run_id: int,
        last_visit_at: datetime,
        page_id: str | None = None,
        server_status: str | None = None,
        last_error: str | None = None,
    ) -> None:
        """Record one terminal POST outcome for deduplication and resumption."""
        await self._db.execute(
            """
            INSERT INTO submissions (url, page_id, outcome, server_status, last_error,
                                     run_id, submitted_at, last_visit_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                page_id = excluded.page_id,
                outcome = excluded.outcome,
                server_status = excluded.server_status,
                last_error = excluded.last_error,
                run_id = excluded.run_id,
                submitted_at = excluded.submitted_at,
                last_visit_at = excluded.last_visit_at
            """,
            (
                url,
                page_id,
                outcome,
                server_status,
                last_error,
                run_id,
                _now_iso(),
                last_visit_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def record_skips(
        self, skips: Iterable[tuple[str, SkipReason]], run_id: int
    ) -> None:
        """Batch-record skipped URLs with the rule each one matched."""
        now = _now_iso()
        await self._db.executemany(
            """
            INSERT INTO skips (url, kind, rule, run_id, skipped_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                kind = excluded.kind, rule = excluded.rule,
                run_id = excluded.run_id, skipped_at = excluded.skipped_at
            """,
            [(url, reason.kind, reason.rule, run_id, now) for url, reason in skips],
        )
        await self._db.commit()

    async def nonterminal_pages(self, *, limit: int) -> list[str]:
        """Return page_ids whose server status is not yet indexed or dead."""
        cursor = await self._db.execute(
            """
            SELECT page_id FROM submissions
            WHERE page_id IS NOT NULL
              AND (server_status IS NULL OR server_status NOT IN ('indexed', 'dead'))
            ORDER BY submitted_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [row[0] for row in await cursor.fetchall()]

    async def update_page_status(
        self, *, page_id: str, status: str, last_error: str | None
    ) -> None:
        """Store the latest server-side lifecycle status for a page."""
        await self._db.execute(
            """
            UPDATE submissions SET server_status = ?, last_error = ?,
                status_checked_at = ?
            WHERE page_id = ?
            """,
            (status, last_error, _now_iso(), page_id),
        )
        await self._db.commit()

    async def status_counts(self) -> dict[str, int]:
        """Count submissions grouped by their last known server status."""
        cursor = await self._db.execute(
            """
            SELECT COALESCE(server_status, outcome), COUNT(*)
            FROM submissions GROUP BY 1 ORDER BY 2 DESC
            """
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}

    async def get_watermark(self, profile: BrowserProfile) -> datetime | None:
        """Return the profile's incremental-import high-water mark, if any."""
        cursor = await self._db.execute(
            """
            SELECT last_visit_at FROM profile_watermarks
            WHERE browser_id = ? AND history_path = ?
            """,
            profile.watermark_key,
        )
        row = await cursor.fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    async def set_watermark(
        self, profile: BrowserProfile, last_visit_at: datetime
    ) -> None:
        """Advance the profile's high-water mark after a clean run."""
        await self._db.execute(
            """
            INSERT INTO profile_watermarks (browser_id, history_path, profile_name,
                                            last_visit_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(browser_id, history_path) DO UPDATE SET
                profile_name = excluded.profile_name,
                last_visit_at = excluded.last_visit_at,
                updated_at = excluded.updated_at
            """,
            (
                *profile.watermark_key,
                profile.profile_name,
                last_visit_at.isoformat(),
                _now_iso(),
            ),
        )
        await self._db.commit()
