"""End-to-end pipeline tests: fixture DBs -> mocked API -> state DB."""

import io
import json

import httpx
import respx
from rich.console import Console

from browser_history_refindery.browsers import BrowserFamily
from browser_history_refindery.config import AppConfig
from browser_history_refindery.pipeline import run_import
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
        times = await state.load_submission_times()
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
