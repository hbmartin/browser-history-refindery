"""End-to-end pipeline tests: fixture DBs -> mocked API -> state DB."""

import io
import json
import sqlite3
from contextlib import closing

import httpx
import pytest
import respx
from rich.console import Console

from browser_history_refindery.api_client import AuthError
from browser_history_refindery.browsers import BrowserFamily
from browser_history_refindery.browsers.base import VisitRecord, to_chromium_us
from browser_history_refindery.config import AppConfig
from browser_history_refindery.pipeline import (
    UrlSubmission,
    _merge_record,
    _runtime_error_from_group,
    run_import,
)
from browser_history_refindery.state import StateStore
from tests.conftest import (
    T0,
    T1,
    T2,
    make_chromium_db,
    make_firefox_db,
    make_safari_db,
    profile_for,
)

BASE = "http://testserver"
SHARED_URL = "https://shared.example/article"


def make_config(tmp_path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "server": {"base_url": BASE, "auth_token": "tok", "ready_timeout": 1.0},
            "pacing": {
                "base_interval": 0.001,
                "floor": 0.001,
                "queue_poll_interval": 0.05,
            },
            "poller": {"interval": 0.02, "drain_grace": 2.0},
            "state": {"db_path": str(tmp_path / "state.sqlite3")},
        }
    )


def make_profiles(tmp_path):
    chrome_db = tmp_path / "chrome-History"
    make_chromium_db(
        chrome_db,
        [
            (SHARED_URL, "Shared (chrome)", [T0, T1], 0),
            ("https://only-chrome.example/", "Chrome only", [T1], 0),
            ("chrome://settings/", None, [T1], 0),
        ],
    )
    safari_db = tmp_path / "safari-History.db"
    make_safari_db(
        safari_db,
        [
            (SHARED_URL, "Shared (safari)", [T2]),
            ("https://blocked.example/", "Blocked", [T1]),
        ],
    )
    return [
        profile_for(chrome_db, BrowserFamily.CHROMIUM),
        profile_for(safari_db, BrowserFamily.SAFARI),
    ]


def quiet_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def _ingest_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body["url"] == "https://blocked.example/":
        return httpx.Response(
            403, json={"error": "blacklisted", "pattern": "blocked.example"}
        )
    page_id = f"pg_{abs(hash(body['url'])) % 10_000}"
    return httpx.Response(202, json={"page_id": page_id, "status": "queued"})


def _status_response(request: httpx.Request, page_id: str) -> httpx.Response:
    return httpx.Response(
        200, json={"page_id": page_id, "status": "indexed", "last_error": None}
    )


def mock_api(router):
    """Wire all routes; returns the POST /v1/pages route for call inspection."""
    router.get("/readyz").respond(200, json={"status": "ready"})
    router.get("/v1/jobs").respond(200, json={"jobs": []})
    router.get(url__regex=r".*/v1/pages/(?P<page_id>[^/]+)/status").mock(
        side_effect=_status_response
    )
    return router.post("/v1/pages").mock(side_effect=_ingest_response)


def test_runtime_only_exception_group_unwraps_first_error():
    first = AuthError()
    grouped = ExceptionGroup(
        "task failures",
        [first, ExceptionGroup("nested", [RuntimeError("other failure")])],
    )
    assert _runtime_error_from_group(grouped) is first
    mixed = ExceptionGroup("mixed", [first, ValueError("invalid response")])
    assert _runtime_error_from_group(mixed) is None


def test_merge_uses_older_nonempty_title_as_fallback(tmp_path):
    url = "https://title.example/"
    profile = profile_for(tmp_path / "History", BrowserFamily.CHROMIUM)
    newer = VisitRecord(
        url=url,
        title=None,
        visit_count=1,
        first_visit_at=T2,
        last_visit_at=T2,
        profile=profile,
    )
    older = VisitRecord(
        url=url,
        title="Older title",
        visit_count=1,
        first_visit_at=T1,
        last_visit_at=T1,
        profile=profile,
    )
    merged: dict[str, UrlSubmission] = {}

    _merge_record(merged, newer)
    _merge_record(merged, older)

    assert merged[url].title == "Older title"
    assert merged[url].last_visit_at == T2


@respx.mock(base_url=BASE)
async def test_full_import_run(respx_mock, tmp_path):
    pages_route = mock_api(respx_mock)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    stats = await run_import(config=config, profiles=profiles, console=quiet_console())

    # 4 distinct http(s) URLs seen; chrome:// skipped; blocked.example 403'd.
    assert stats.total_to_submit == 3
    assert stats.accepted == 2
    assert stats.blacklisted == 1
    assert stats.skipped == 1
    assert stats.errors == 0
    assert stats.indexed == 2

    # The shared URL merged across both browsers: newest source is Safari.
    posts = [json.loads(call.request.content) for call in pages_route.calls]
    shared = next(body for body in posts if body["url"] == SHARED_URL)
    assert shared["source"] == "history-import:test-safari"
    assert shared["metadata"]["visit_count"] == 3
    assert len(shared["metadata"]["sources"]) == 2
    assert shared["title"] == "Shared (safari)"

    async with StateStore(config.state.db_path) as state:
        counts = await state.status_counts()
        times = await state.load_submission_visit_times()
        watermark = await state.get_watermark(profiles[0])
    assert counts.get("indexed") == 2
    assert counts.get("blacklisted") == 1
    assert SHARED_URL in times
    assert watermark == T1  # newest chrome visit


@respx.mock(base_url=BASE)
async def test_second_run_is_incremental(respx_mock, tmp_path):
    mock_api(respx_mock)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    first = await run_import(config=config, profiles=profiles, console=quiet_console())
    assert first.accepted == 2

    second = await run_import(config=config, profiles=profiles, console=quiet_console())
    # Watermarks advanced: nothing new to read, nothing re-submitted.
    assert second.total_to_submit == 0
    assert second.submitted == 0


@respx.mock(base_url=BASE)
async def test_limited_run_leaves_watermarks_for_remaining_urls(respx_mock, tmp_path):
    mock_api(respx_mock)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    first = await run_import(
        config=config, profiles=profiles, console=quiet_console(), limit=1
    )
    assert first.total_to_submit == 1
    async with StateStore(config.state.db_path) as state:
        assert await state.get_watermark(profiles[0]) is None
        assert await state.get_watermark(profiles[1]) is None

    second = await run_import(config=config, profiles=profiles, console=quiet_console())
    assert second.total_to_submit == 2
    assert second.accepted == 1
    assert second.blacklisted == 1


@respx.mock(base_url=BASE)
async def test_limited_run_keeps_watermarks_for_fully_submitted_profiles(
    respx_mock, tmp_path
):
    mock_api(respx_mock)
    config = make_config(tmp_path)
    old_db = tmp_path / "old-History"
    make_chromium_db(old_db, [("https://old.example/", "Old", [T0], 0)])
    new_db = tmp_path / "new-places.sqlite"
    make_firefox_db(new_db, [("https://new.example/", "New", [T1], 0)])
    old_profile = profile_for(old_db, BrowserFamily.CHROMIUM)
    new_profile = profile_for(new_db, BrowserFamily.FIREFOX)

    stats = await run_import(
        config=config,
        profiles=[old_profile, new_profile],
        console=quiet_console(),
        limit=1,
    )

    # The newest URL (firefox) made the cut, so only the chromium profile —
    # whose URL was dropped — must be re-read on the next run.
    assert stats.total_to_submit == 1
    assert stats.accepted == 1
    async with StateStore(config.state.db_path) as state:
        assert await state.get_watermark(old_profile) is None
        assert await state.get_watermark(new_profile) == T1


@respx.mock(base_url=BASE)
async def test_revisit_uses_last_observed_visit_time(respx_mock, tmp_path):
    pages_route = mock_api(respx_mock)
    config = make_config(tmp_path)
    config.import_.resubmit_revisits = True
    db = tmp_path / "History"
    url = "https://revisited.example/"
    make_chromium_db(db, [(url, "Revisited", [T0], 0)])
    profile = profile_for(db, BrowserFamily.CHROMIUM)

    first = await run_import(config=config, profiles=[profile], console=quiet_console())
    assert first.accepted == 1
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO visits (url, visit_time) VALUES (?, ?)",
            (1, to_chromium_us(T1)),
        )
        conn.commit()

    second = await run_import(
        config=config, profiles=[profile], console=quiet_console()
    )
    assert second.total_to_submit == 1
    assert second.accepted == 1
    assert len(pages_route.calls) == 2
    async with StateStore(config.state.db_path) as state:
        observed = await state.load_submission_visit_times()
    assert observed[url] == T1


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_task_group_unwraps_auth_error(respx_mock, tmp_path):
    respx_mock.get("/readyz").respond(200, json={"status": "ready"})
    respx_mock.get("/v1/jobs").respond(200, json={"jobs": []})
    respx_mock.post("/v1/pages").respond(
        401, json={"detail": "missing or invalid bearer token"}
    )
    config = make_config(tmp_path)
    config.import_.resubmit_revisits = True
    db = tmp_path / "History"
    make_chromium_db(db, [("https://auth.example/", "Auth", [T0], 0)])

    with pytest.raises(AuthError):
        await run_import(
            config=config,
            profiles=[profile_for(db, BrowserFamily.CHROMIUM)],
            console=quiet_console(),
        )


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_validation_rejection_is_terminal_and_not_retried(respx_mock, tmp_path):
    respx_mock.get("/readyz").respond(200, json={"status": "ready"})
    respx_mock.get("/v1/jobs").respond(200, json={"jobs": []})
    pages_route = respx_mock.post("/v1/pages").respond(
        422, json={"detail": "URL is not ingestible"}
    )
    config = make_config(tmp_path)
    db = tmp_path / "History"
    url = "https://rejected.example/"
    make_chromium_db(db, [(url, "Rejected", [T0], 0)])
    profile = profile_for(db, BrowserFamily.CHROMIUM)

    first = await run_import(config=config, profiles=[profile], console=quiet_console())

    assert first.submitted == 1
    assert first.rejected == 1
    assert first.errors == 0
    assert first.processed == 1
    async with StateStore(config.state.db_path) as state:
        counts = await state.status_counts()
        watermark = await state.get_watermark(profile)
    assert counts["rejected"] == 1
    assert watermark == T0
    with closing(sqlite3.connect(config.state.db_path)) as conn:
        run_row = conn.execute(
            "SELECT interrupted, rejected, errors FROM runs WHERE id = 1"
        ).fetchone()
        submission_row = conn.execute(
            "SELECT outcome, last_error FROM submissions WHERE url = :url",
            {"url": url},
        ).fetchone()
    assert run_row == (0, 1, 0)
    assert submission_row == (
        "rejected",
        "server rejected the request: URL is not ingestible",
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO visits (url, visit_time) VALUES (?, ?)",
            (1, to_chromium_us(T1)),
        )
        conn.commit()

    second = await run_import(
        config=config,
        profiles=[profile],
        console=quiet_console(),
        ignore_watermarks=True,
    )

    assert second.total_to_submit == 0
    assert second.previously_rejected == 1
    assert second.already_submitted == 0
    assert len(pages_route.calls) == 1


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_dry_run_submits_nothing(respx_mock, tmp_path):
    pages_route = mock_api(respx_mock)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    stats = await run_import(
        config=config, profiles=profiles, console=quiet_console(), dry_run=True
    )
    assert stats.total_to_submit == 3
    assert not pages_route.calls
