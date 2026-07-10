"""End-to-end pipeline tests: fixture DBs -> mocked API -> state DB."""

import io
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx2
import pytest
from pytest_httpx2 import HTTPXMock
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

    # The shared URL merged across both browsers: newest source is Safari.
    posts = [json.loads(request.content) for request in ingest_requests(httpx2_mock)]
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


async def test_dry_run_submits_nothing(httpx2_mock: HTTPXMock, tmp_path: Path) -> None:
    mock_api(httpx2_mock, is_optional=True)
    config = make_config(tmp_path)
    profiles = make_profiles(tmp_path)

    stats = await run_import(
        config=config, profiles=profiles, console=quiet_console(), dry_run=True
    )
    assert stats.total_to_submit == 3
    assert not ingest_requests(httpx2_mock)
