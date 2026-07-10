"""End-to-end pipeline tests: fixture DBs -> mocked API -> state DB."""

import asyncio
import io
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from pytest_httpx2 import HTTPXMock
from rich.console import Console

from browser_history_refindery.api_client import Accepted, AuthError, Revisit
from browser_history_refindery.api_models import IngestPageRequest
from browser_history_refindery.browsers import BrowserFamily
from browser_history_refindery.browsers.base import VisitRecord, to_chromium_us
from browser_history_refindery.config import AppConfig
from browser_history_refindery.filters import ExclusionEngine
from browser_history_refindery.pipeline import (
    UrlSubmission,
    _log_url,
    _merge_record,
    _Planner,
    _Runner,
    _runtime_error_from_group,
    _stream_profiles,
    run_import,
)
from browser_history_refindery.state import StateStore
from browser_history_refindery.stats import RunStats
from tests.conftest import (
    T0,
    T1,
    T2,
    make_chromium_db,
    make_safari_db,
    profile_for,
)

BASE = "http://testserver"
SHARED_URL = "https://shared.example/article"


def endpoint(path: str) -> str:
    return f"{BASE}{path}"


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


def _ingest_response(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    if body["url"] == "https://blocked.example/":
        return httpx2.Response(
            403, json={"error": "blacklisted", "pattern": "blocked.example"}
        )
    page_id = f"pg_{abs(hash(body['url'])) % 10_000}"
    return httpx2.Response(202, json={"page_id": page_id, "status": "queued"})


def _status_response(request: httpx2.Request) -> httpx2.Response:
    page_id = request.url.path.rsplit("/", maxsplit=2)[-2]
    return httpx2.Response(
        200, json={"page_id": page_id, "status": "indexed", "last_error": None}
    )


def mock_preflight(
    httpx2_mock: HTTPXMock,
    *,
    ready_optional: bool = False,
    jobs_optional: bool = False,
) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=200,
        json={"status": "ready"},
        is_optional=ready_optional,
        is_reusable=True,
    )
    httpx2_mock.add_response(
        method="GET",
        url=re.compile(rf"{re.escape(endpoint('/v1/jobs'))}(?:\?.*)?"),
        status_code=200,
        json={"jobs": []},
        is_optional=jobs_optional,
        is_reusable=True,
    )


def mock_api(httpx2_mock: HTTPXMock, *, is_optional: bool = False) -> None:
    """Wire all import API routes."""
    mock_preflight(
        httpx2_mock,
        ready_optional=is_optional,
        jobs_optional=is_optional,
    )
    httpx2_mock.add_callback(
        _status_response,
        method="GET",
        url=re.compile(rf"{re.escape(BASE)}/v1/pages/[^/]+/status"),
        is_optional=is_optional,
        is_reusable=True,
    )
    httpx2_mock.add_callback(
        _ingest_response,
        method="POST",
        url=endpoint("/v1/pages"),
        is_optional=is_optional,
        is_reusable=True,
    )


def ingest_requests(httpx2_mock: HTTPXMock) -> list[httpx2.Request]:
    return httpx2_mock.get_requests(method="POST", url=endpoint("/v1/pages"))


def test_runtime_only_exception_group_unwraps_first_error():
    first = AuthError()
    grouped = ExceptionGroup(
        "task failures",
        [first, ExceptionGroup("nested", [RuntimeError("other failure")])],
    )
    assert _runtime_error_from_group(grouped) is first
    mixed = ExceptionGroup("mixed", [first, ValueError("invalid response")])
    assert _runtime_error_from_group(mixed) is None


def test_watermark_key_normalizes_history_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    absolute_profile = profile_for(
        tmp_path / "profile" / "History", BrowserFamily.CHROMIUM
    )
    monkeypatch.chdir(tmp_path)
    relative_profile = profile_for(Path("profile/History"), BrowserFamily.CHROMIUM)

    assert relative_profile.watermark_key == absolute_profile.watermark_key


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


def test_merge_profile_deduplicates_touched_urls(tmp_path: Path) -> None:
    profile = profile_for(tmp_path / "History", BrowserFamily.CHROMIUM)
    url = "https://duplicate.example/page?version=1"
    records = [
        VisitRecord(
            url=url,
            title="First",
            visit_count=1,
            first_visit_at=T0,
            last_visit_at=visited_at,
            profile=profile,
        )
        for visited_at in (T0, T1)
    ]
    planner = _Planner(
        engine=ExclusionEngine(AppConfig().exclusions),
        stats=RunStats(),
        submitted={},
        permanently_rejected=set(),
        resubmit_revisits=False,
    )

    _, touched = planner.merge_profile(records)
    _, enqueue = planner.classify_profile(touched)

    assert touched == [url]
    assert enqueue == [url]


def test_merge_profile_keeps_query_variants_distinct(tmp_path: Path) -> None:
    profile = profile_for(tmp_path / "History", BrowserFamily.CHROMIUM)
    urls = [
        "https://queries.example/page?version=1",
        "https://queries.example/page?version=2",
    ]
    records = [
        VisitRecord(
            url=url,
            title="Versioned",
            visit_count=1,
            first_visit_at=T0,
            last_visit_at=T0,
            profile=profile,
        )
        for url in urls
    ]
    planner = _Planner(
        engine=ExclusionEngine(AppConfig().exclusions),
        stats=RunStats(),
        submitted={},
        permanently_rejected=set(),
        resubmit_revisits=False,
    )

    _, touched = planner.merge_profile(records)
    _, enqueue = planner.classify_profile(touched)

    assert set(touched) == set(urls)
    assert set(enqueue) == set(urls)
    assert set(planner.merged) == set(urls)


def test_merge_profile_does_not_requeue_an_already_queued_revisit(
    tmp_path: Path,
) -> None:
    profile = profile_for(tmp_path / "History", BrowserFamily.CHROMIUM)
    url = "https://queued-revisit.example/"
    planner = _Planner(
        engine=ExclusionEngine(AppConfig().exclusions),
        stats=RunStats(),
        submitted={},
        permanently_rejected=set(),
        resubmit_revisits=True,
    )
    initial = VisitRecord(
        url=url,
        title="Initial",
        visit_count=1,
        first_visit_at=T0,
        last_visit_at=T0,
        profile=profile,
    )
    newer = VisitRecord(
        url=url,
        title="Newer",
        visit_count=1,
        first_visit_at=T2,
        last_visit_at=T2,
        profile=profile,
    )

    _, touched = planner.merge_profile([initial])
    planner.classify_profile(touched)
    submission = planner.merged[url]
    submission.last_submitted_visit_at = T0
    submission.queued = True

    _, reconsidered = planner.merge_profile([newer])

    assert reconsidered == []
    assert submission.last_visit_at == T2
    assert submission.queued is True


def test_log_url_hides_query_but_preserves_distinct_fingerprints() -> None:
    first = "https://example.test/page?token=secret-one#private"
    second = "https://example.test/page?token=secret-two#private"

    first_display = _log_url(first)
    second_display = _log_url(second)

    assert first_display.startswith("https://example.test/page?redacted=")
    assert "secret-one" not in first_display
    assert "private" not in first_display
    assert first_display != second_display


async def test_full_import_run(httpx2_mock: HTTPXMock, tmp_path: Path) -> None:
    mock_api(httpx2_mock)
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

    # The shared URL is submitted exactly once. Under streaming the fully-merged
    # cross-profile shape is timing-dependent (see the dedicated merge test that
    # exercises _stream_profiles directly), so we only assert it was sent once.
    posts = [json.loads(request.content) for request in ingest_requests(httpx2_mock)]
    assert sum(body["url"] == SHARED_URL for body in posts) == 1

    async with StateStore(config.state.db_path) as state:
        counts = await state.status_counts()
        times = await state.load_submission_visit_times()
        watermark = await state.get_watermark(profiles[0])
    assert counts.get("indexed") == 2
    assert counts.get("blacklisted") == 1
    assert SHARED_URL in times
    assert watermark == T1  # newest chrome visit


async def test_second_run_is_incremental(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_api(httpx2_mock)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    first = await run_import(config=config, profiles=profiles, console=quiet_console())
    assert first.accepted == 2

    second = await run_import(config=config, profiles=profiles, console=quiet_console())
    # Watermarks advanced: nothing new to read, nothing re-submitted.
    assert second.total_to_submit == 0
    assert second.submitted == 0


async def test_limited_run_leaves_watermarks_for_remaining_urls(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_api(httpx2_mock)
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


async def test_limited_run_keeps_watermarks_for_fully_submitted_profiles(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_api(httpx2_mock)
    config = make_config(tmp_path)
    old_db = tmp_path / "old-History"
    make_chromium_db(old_db, [("https://old.example/", "Old", [T0], 0)])
    new_db = tmp_path / "new-History"
    make_chromium_db(new_db, [("https://new.example/", "New", [T1], 0)])
    old_profile = profile_for(old_db, BrowserFamily.CHROMIUM)
    new_profile = profile_for(new_db, BrowserFamily.CHROMIUM)

    assert old_profile.key == new_profile.key
    assert old_profile.watermark_key != new_profile.watermark_key

    stats = await run_import(
        config=config,
        profiles=[old_profile, new_profile],
        console=quiet_console(),
        limit=1,
    )

    # The newest URL made the cut, so only the profile whose URL was dropped
    # must be re-read on the next run, even though both use the same family and
    # profile directory name.
    assert stats.total_to_submit == 1
    assert stats.accepted == 1
    async with StateStore(config.state.db_path) as state:
        assert await state.get_watermark(old_profile) is None
        assert await state.get_watermark(new_profile) == T1


async def test_revisit_uses_last_observed_visit_time(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_api(httpx2_mock)
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
    assert len(ingest_requests(httpx2_mock)) == 2
    async with StateStore(config.state.db_path) as state:
        observed = await state.load_submission_visit_times()
    assert observed[url] == T1


async def test_shared_url_reconsiders_later_profile_revisit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.import_.resubmit_revisits = True
    url = "https://cross-profile-revisit.example/"
    older_db = tmp_path / "older-History"
    make_chromium_db(older_db, [(url, "Older", [T0], 0)])
    newer_db = tmp_path / "newer-History"
    make_chromium_db(newer_db, [(url, "Newer", [T2], 0)])
    profiles = [
        profile_for(older_db, BrowserFamily.CHROMIUM),
        profile_for(newer_db, BrowserFamily.CHROMIUM),
    ]
    collected: list[UrlSubmission] = []
    stats = RunStats()

    async def collect(submission: UrlSubmission) -> None:
        collected.append(submission)

    async with StateStore(config.state.db_path) as state:
        prior_run_id = await state.begin_run()
        await state.record_submission(
            url=url,
            outcome="queued",
            run_id=prior_run_id,
            last_visit_at=T1,
            page_id="pg_prior",
            server_status="indexed",
        )
        run_id = await state.begin_run()
        await _stream_profiles(
            profiles,
            engine=ExclusionEngine(config.exclusions),
            state=state,
            stats=stats,
            config=config,
            run_id=run_id,
            ignore_watermarks=False,
            limit=None,
            shutdown=asyncio.Event(),
            emit=collect,
        )

    assert len(collected) == 1
    assert collected[0].last_visit_at == T2
    assert stats.already_submitted == 0
    assert stats.total_to_submit == 1


async def test_submit_persists_the_attempted_snapshot(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.import_.resubmit_revisits = True
    url = "https://in-flight-merge.example/"
    first_profile = profile_for(tmp_path / "first-History", BrowserFamily.CHROMIUM)
    later_profile = profile_for(tmp_path / "later-History", BrowserFamily.SAFARI)
    initial = VisitRecord(
        url=url,
        title="Initial",
        visit_count=1,
        first_visit_at=T0,
        last_visit_at=T0,
        profile=first_profile,
    )
    later = VisitRecord(
        url=url,
        title="Later",
        visit_count=1,
        first_visit_at=T2,
        last_visit_at=T2,
        profile=later_profile,
    )
    merged: dict[str, UrlSubmission] = {}
    _merge_record(merged, initial)
    item = merged[url]
    attempted_requests: list[IngestPageRequest] = []

    class MutatingClient:
        async def ingest_url(self, request: IngestPageRequest) -> Accepted:
            attempted_requests.append(request)
            _merge_record(merged, later)
            await asyncio.sleep(0)
            return Accepted(page_id="pg_snapshot")

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        runner = _Runner(
            client=cast("Any", MutatingClient()),
            state=state,
            stats=RunStats(),
            config=config,
            run_id=run_id,
            profiles=[],
            engine=ExclusionEngine(config.exclusions),
            ignore_watermarks=False,
            limit=None,
        )
        await runner._submit_one(item)  # noqa: SLF001 - targeted runner regression
        observed = await state.load_submission_visit_times()

    assert attempted_requests[0].fetched_at == T0
    assert item.last_visit_at == T2
    assert observed[url] == T0
    assert runner.queue.get_nowait() is item
    assert item.queued is True
    assert runner.stats.total_to_submit == 1


async def test_submitter_rejects_duplicate_queueing_and_restores_reserved_item(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    profile = profile_for(tmp_path / "History", BrowserFamily.CHROMIUM)
    merged: dict[str, UrlSubmission] = {}
    _merge_record(
        merged,
        VisitRecord(
            url="https://reserved.example/",
            title="Reserved",
            visit_count=1,
            first_visit_at=T0,
            last_visit_at=T0,
            profile=profile,
        ),
    )
    item = merged["https://reserved.example/"]

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        runner = _Runner(
            client=cast("Any", object()),
            state=state,
            stats=RunStats(),
            config=config,
            run_id=run_id,
            profiles=[],
            engine=ExclusionEngine(config.exclusions),
            ignore_watermarks=False,
            limit=None,
        )
        assert runner._enqueue(item) is True  # noqa: SLF001 - queue-state regression
        assert runner._enqueue(item) is False  # noqa: SLF001 - queue-state regression

        async def stop_after_reservation() -> None:
            runner.shutdown.set()

        cast("Any", runner.pacer).wait = stop_after_reservation
        await runner.submitter()

    assert runner.queue.qsize() == 1
    assert runner.queue.get_nowait() is item
    assert item.queued is True
    assert runner.stats.submitter_finished is True


async def test_submit_requeues_on_revisit_when_newer_visit_appears(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    config.import_.resubmit_revisits = True
    url = "https://revisit-requeue.example/"
    first_profile = profile_for(tmp_path / "first-History", BrowserFamily.CHROMIUM)
    later_profile = profile_for(tmp_path / "later-History", BrowserFamily.SAFARI)
    initial = VisitRecord(
        url=url,
        title="Initial",
        visit_count=1,
        first_visit_at=T0,
        last_visit_at=T0,
        profile=first_profile,
    )
    later = VisitRecord(
        url=url,
        title="Later",
        visit_count=1,
        first_visit_at=T2,
        last_visit_at=T2,
        profile=later_profile,
    )
    merged: dict[str, UrlSubmission] = {}
    _merge_record(merged, initial)
    item = merged[url]

    class RevisitClient:
        async def ingest_url(self, _request: IngestPageRequest) -> Revisit:
            _merge_record(merged, later)
            await asyncio.sleep(0)
            return Revisit(
                page_id="pg_revisit", status="indexed", content_hash_differs=False
            )

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        runner = _Runner(
            client=cast("Any", RevisitClient()),
            state=state,
            stats=RunStats(),
            config=config,
            run_id=run_id,
            profiles=[],
            engine=ExclusionEngine(config.exclusions),
            ignore_watermarks=False,
            limit=None,
        )
        await runner._submit_one(item)  # noqa: SLF001 - targeted runner regression
        observed = await state.load_submission_visit_times()

    assert runner.stats.revisits == 1
    assert item.last_submitted_visit_at == T0
    assert observed[url] == T0
    assert runner.queue.get_nowait() is item
    assert item.queued is True
    assert runner.stats.total_to_submit == 1


async def test_task_group_unwraps_auth_error(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_preflight(httpx2_mock, jobs_optional=True)
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=401,
        json={"detail": "missing or invalid bearer token"},
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


async def test_validation_rejection_is_terminal_and_not_retried(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_preflight(httpx2_mock, jobs_optional=True)
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=422,
        json={"detail": "URL is not ingestible"},
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
    assert len(ingest_requests(httpx2_mock)) == 1


async def test_transient_server_error_is_retried_then_succeeds(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_preflight(httpx2_mock, jobs_optional=True)
    httpx2_mock.add_response(
        method="POST", url=endpoint("/v1/pages"), status_code=503, json={}
    )  # single-use: the first attempt fails
    httpx2_mock.add_callback(
        _ingest_response,
        method="POST",
        url=endpoint("/v1/pages"),
        is_reusable=True,
    )
    httpx2_mock.add_callback(
        _status_response,
        method="GET",
        url=re.compile(rf"{re.escape(BASE)}/v1/pages/[^/]+/status"),
        is_optional=True,
        is_reusable=True,
    )
    config = make_config(tmp_path)
    db = tmp_path / "History"
    make_chromium_db(db, [("https://flaky.example/", "Flaky", [T0], 0)])

    stats = await run_import(
        config=config,
        profiles=[profile_for(db, BrowserFamily.CHROMIUM)],
        console=quiet_console(),
    )

    assert stats.retries == 1
    assert stats.accepted == 1
    assert stats.errors == 0
    assert len(ingest_requests(httpx2_mock)) == 2


async def test_repeated_errors_give_up_and_hold_watermark(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    mock_preflight(httpx2_mock, jobs_optional=True)
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=503,
        json={},
        is_reusable=True,  # every attempt fails
    )
    config = make_config(tmp_path)
    config.pacing.max_attempts = 2
    db = tmp_path / "History"
    profile = profile_for(db, BrowserFamily.CHROMIUM)
    make_chromium_db(db, [("https://down.example/", "Down", [T0], 0)])

    stats = await run_import(config=config, profiles=[profile], console=quiet_console())

    assert stats.retries == 1  # one retry, then the second attempt gives up
    assert stats.errors == 1
    assert stats.accepted == 0
    assert len(ingest_requests(httpx2_mock)) == 2
    async with StateStore(config.state.db_path) as state:
        # An errored run must not advance the watermark.
        assert await state.get_watermark(profile) is None


async def test_dry_run_submits_nothing(httpx2_mock: HTTPXMock, tmp_path: Path) -> None:
    mock_api(httpx2_mock, is_optional=True)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    stats = await run_import(
        config=config, profiles=profiles, console=quiet_console(), dry_run=True
    )
    assert stats.total_to_submit == 3
    assert stats.urls_read_total == 5  # 3 chrome rows + 2 safari rows
    assert stats.unique_urls == 4  # SHARED_URL merges across the two browsers
    assert not ingest_requests(httpx2_mock)


async def test_stream_profiles_merges_across_profiles(tmp_path: Path) -> None:
    """_stream_profiles fully merges a shared URL once every profile is read."""
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)
    stats = RunStats()
    collected: list[UrlSubmission] = []

    async def collect(submission: UrlSubmission) -> None:
        collected.append(submission)

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        watermarks = await _stream_profiles(
            profiles,
            engine=ExclusionEngine(config.exclusions),
            state=state,
            stats=stats,
            config=config,
            run_id=run_id,
            ignore_watermarks=False,
            limit=None,
            shutdown=asyncio.Event(),
            emit=collect,
        )

    assert stats.reading_finished
    shared = next(
        submission for submission in collected if submission.url == SHARED_URL
    )
    assert shared.primary.browser == "test-safari"
    assert shared.total_visits == 3
    assert len(shared.sources) == 2
    assert shared.title == "Shared (safari)"
    # Both profiles were fully read, so both offer a watermark.
    assert set(watermarks.values()) == {T1, T2}


async def test_streaming_emits_first_profile_before_reading_second(
    tmp_path: Path,
) -> None:
    """A profile's URLs are emitted before the next profile is read."""
    config = make_config(tmp_path)
    first_db = tmp_path / "first-History"
    make_chromium_db(first_db, [("https://first.example/", "First", [T0], 0)])
    second_db = tmp_path / "second-History"
    make_chromium_db(second_db, [("https://second.example/", "Second", [T1], 0)])
    profiles = [
        profile_for(first_db, BrowserFamily.CHROMIUM),
        profile_for(second_db, BrowserFamily.CHROMIUM),
    ]
    # Same family + profile dir name means distinct history paths but one stats key.
    order: list[str] = []
    stats = RunStats()

    async def record(submission: UrlSubmission) -> None:
        order.append(submission.url)

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        await _stream_profiles(
            profiles,
            engine=ExclusionEngine(config.exclusions),
            state=state,
            stats=stats,
            config=config,
            run_id=run_id,
            ignore_watermarks=False,
            limit=None,
            shutdown=asyncio.Event(),
            emit=record,
        )

    # Unbounded streaming preserves profile order (not a global newest-first sort).
    assert order == ["https://first.example/", "https://second.example/"]


async def test_stream_profiles_stops_on_shutdown(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)
    stats = RunStats()
    shutdown = asyncio.Event()
    shutdown.set()  # already requested before any profile is read

    async def collect(_submission: UrlSubmission) -> None:
        pytest.fail("nothing should be emitted after shutdown")

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        watermarks = await _stream_profiles(
            profiles,
            engine=ExclusionEngine(config.exclusions),
            state=state,
            stats=stats,
            config=config,
            run_id=run_id,
            ignore_watermarks=False,
            limit=None,
            shutdown=shutdown,
            emit=collect,
        )

    assert stats.profiles_read == 0
    assert stats.total_to_submit == 0
    assert watermarks == {}
    assert stats.reading_finished  # the phase still completes (with no work)


async def test_limited_direct_keeps_global_newest(tmp_path: Path) -> None:
    """The bounded path emits the newest URLs across all profiles, in order."""
    config = make_config(tmp_path)
    older = tmp_path / "older-History"
    make_chromium_db(older, [("https://older.example/", "Older", [T0], 0)])
    newer = tmp_path / "newer-History"
    make_chromium_db(newer, [("https://newer.example/", "Newer", [T2], 0)])
    profiles = [
        profile_for(older, BrowserFamily.CHROMIUM),
        profile_for(newer, BrowserFamily.CHROMIUM),
    ]
    stats = RunStats()
    order: list[str] = []

    async def record(submission: UrlSubmission) -> None:
        order.append(submission.url)

    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        watermarks = await _stream_profiles(
            profiles,
            engine=ExclusionEngine(config.exclusions),
            state=state,
            stats=stats,
            config=config,
            run_id=run_id,
            ignore_watermarks=False,
            limit=1,
            shutdown=asyncio.Event(),
            emit=record,
        )

    # Even though the older profile is read first, only the globally-newest URL
    # is emitted, and the dropped profile keeps no watermark.
    assert order == ["https://newer.example/"]
    assert set(watermarks.values()) == {T2}
    assert profiles[0] not in watermarks
