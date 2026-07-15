"""State store CRUD and semantics."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from browser_history_refindery.api_models import EstimateFallbackProfile
from browser_history_refindery.browsers.base import BrowserFamily, BrowserProfile
from browser_history_refindery.filters import SkipKind, SkipReason
from browser_history_refindery.state import StateSchemaTooNewError, StateStore


@pytest.fixture
async def store(tmp_path):
    async with StateStore(tmp_path / "state.sqlite3") as state:
        yield state


def _profile(tmp_path) -> BrowserProfile:
    return BrowserProfile(
        browser_id="chrome",
        browser_label="Google Chrome",
        profile_dir="Default",
        profile_name="Harold",
        history_path=tmp_path / "History",
        family=BrowserFamily.CHROMIUM,
    )


async def test_run_lifecycle(store):
    run_id = await store.begin_run()
    assert run_id > 0
    await store.finish_run(
        run_id,
        interrupted=False,
        urls_seen=10,
        submitted=5,
        revisits=2,
        blacklisted=1,
        rejected=1,
        skipped=2,
        errors=0,
    )


async def test_submissions_and_nonterminal(store):
    run_id = await store.begin_run()
    first_visit = datetime(2026, 1, 1, tzinfo=UTC)
    second_visit = datetime(2026, 2, 1, tzinfo=UTC)
    await store.record_submission(
        url="https://a.example/",
        outcome="queued",
        run_id=run_id,
        last_visit_at=first_visit,
        page_id="pg_a",
        server_status="queued",
    )
    await store.record_submission(
        url="https://b.example/",
        outcome="blacklisted",
        run_id=run_id,
        last_visit_at=second_visit,
    )
    times = await store.load_submission_visit_times()
    assert set(times) == {"https://a.example/", "https://b.example/"}
    assert times["https://a.example/"] == first_visit
    assert times["https://b.example/"] == second_visit
    assert await store.nonterminal_pages(limit=10) == ["pg_a"]

    await store.update_page_status(page_id="pg_a", status="indexed", last_error=None)
    assert await store.nonterminal_pages(limit=10) == []
    counts = await store.status_counts()
    assert counts["indexed"] == 1
    assert counts["blacklisted"] == 1


async def test_record_skips_upsert(store):
    run_id = await store.begin_run()
    reason = SkipReason(kind=SkipKind.CATEGORY, rule="category:banking domain=x.com")
    await store.record_skips([("https://x.com/", reason)], run_id)
    await store.record_skips([("https://x.com/", reason)], run_id)  # idempotent


async def test_v1_state_migrates_submission_visit_times(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    submitted_at = datetime(2026, 4, 1, tzinfo=UTC)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                interrupted INTEGER NOT NULL DEFAULT 0,
                urls_seen INTEGER NOT NULL DEFAULT 0,
                submitted INTEGER NOT NULL DEFAULT 0,
                revisits INTEGER NOT NULL DEFAULT 0,
                blacklisted INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE submissions (
                url TEXT PRIMARY KEY,
                page_id TEXT,
                outcome TEXT NOT NULL,
                server_status TEXT,
                last_error TEXT,
                run_id INTEGER NOT NULL REFERENCES runs(id),
                submitted_at TEXT NOT NULL,
                status_checked_at TEXT
            );
            PRAGMA user_version = 1;
            """
        )
        conn.execute(
            "INSERT INTO runs (id, started_at) VALUES (1, ?)",
            (submitted_at.isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO submissions (url, outcome, run_id, submitted_at)
            VALUES (?, 'queued', 1, ?)
            """,
            ("https://legacy.example/", submitted_at.isoformat()),
        )
        conn.commit()

    async with StateStore(db_path) as state:
        times = await state.load_submission_visit_times()
    assert times["https://legacy.example/"] == submitted_at
    with closing(sqlite3.connect(db_path)) as conn:
        run_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    assert "rejected" in run_columns
    assert schema_version == 4


def _estimate_profile(*, fingerprint: str = "config-a") -> EstimateFallbackProfile:
    return EstimateFallbackProfile(
        config_fingerprint=fingerprint,
        generated_at="2026-07-14T12:00:00Z",
        storage_bytes_per_page=2_048,
        cost_usd_per_page=Decimal("0.0015"),
        cost_breakdown_usd_per_page={"embedding": Decimal("0.0015")},
    )


async def test_estimation_profile_cache_is_keyed_and_replaced(store) -> None:
    first = _estimate_profile()
    await store.set_estimation_profile(
        server_base_url="https://one.example/", profile=first
    )
    assert (
        await store.get_estimation_profile(server_base_url="https://one.example")
        == first
    )
    assert (
        await store.get_estimation_profile(server_base_url="https://two.example")
        is None
    )

    replacement = _estimate_profile(fingerprint="config-b")
    await store.set_estimation_profile(
        server_base_url="https://one.example", profile=replacement
    )
    assert (
        await store.get_estimation_profile(server_base_url="https://one.example")
        == replacement
    )


async def test_invalid_cached_estimation_profile_is_ignored(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    async with StateStore(db_path) as state:
        await state.set_estimation_profile(
            server_base_url="https://one.example", profile=_estimate_profile()
        )
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "UPDATE estimation_profiles SET profile_json = ?",
            ('{"storage_bytes_per_page": -1}',),
        )
        conn.commit()
    async with StateStore(db_path) as state:
        assert (
            await state.get_estimation_profile(server_base_url="https://one.example")
            is None
        )


async def test_open_refuses_newer_schema(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()

    with pytest.raises(StateSchemaTooNewError, match="schema is v99"):
        async with StateStore(db_path):
            pass


async def test_watermarks(store, tmp_path):
    profile = _profile(tmp_path)
    assert await store.get_watermark(profile) is None
    moment = datetime(2026, 5, 1, tzinfo=UTC)
    await store.set_watermark(profile, moment)
    assert await store.get_watermark(profile) == moment
    later = datetime(2026, 6, 1, tzinfo=UTC)
    await store.set_watermark(profile, later)
    assert await store.get_watermark(profile) == later
