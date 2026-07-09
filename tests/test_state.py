"""State store CRUD and semantics."""

from datetime import UTC, datetime

import pytest

from browser_history_refindery.browsers.base import BrowserFamily, BrowserProfile
from browser_history_refindery.filters import SkipKind, SkipReason
from browser_history_refindery.state import StateStore


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
        skipped=2,
        errors=0,
    )


async def test_submissions_and_nonterminal(store):
    run_id = await store.begin_run()
    await store.record_submission(
        url="https://a.example/",
        outcome="queued",
        run_id=run_id,
        page_id="pg_a",
        server_status="queued",
    )
    await store.record_submission(
        url="https://b.example/", outcome="blacklisted", run_id=run_id
    )
    times = await store.load_submission_times()
    assert set(times) == {"https://a.example/", "https://b.example/"}
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


async def test_watermarks(store, tmp_path):
    profile = _profile(tmp_path)
    assert await store.get_watermark(profile) is None
    moment = datetime(2026, 5, 1, tzinfo=UTC)
    await store.set_watermark(profile, moment)
    assert await store.get_watermark(profile) == moment
    later = datetime(2026, 6, 1, tzinfo=UTC)
    await store.set_watermark(profile, later)
    assert await store.get_watermark(profile) == later
