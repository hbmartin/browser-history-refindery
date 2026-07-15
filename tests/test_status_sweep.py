"""status-sweep command: batch status refresh and the 0.2.0 capability gate."""

import io
from pathlib import Path

import pytest
from pytest_httpx2 import HTTPXMock
from rich.console import Console

from browser_history_refindery.api_client import RequiresBatchApiError
from browser_history_refindery.commands import status_sweep
from browser_history_refindery.state import StateStore
from tests.conftest import T0

BASE = "http://testserver"

_READYZ_WITH_BATCH = {
    "status": "ready",
    "capabilities": {"batch_ingest": True, "batch_status": True},
}


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def _write_config(config_path: Path, db_path: Path) -> None:
    config_path.write_text(
        f'[server]\nbase_url = "{BASE}"\nauth_token = "tok"\nready_timeout = 1.0\n'
        f'[state]\ndb_path = "{db_path.as_posix()}"\n'
        "[logging]\nenabled = false\n",
        encoding="utf-8",
    )


async def _seed_pending(db_path: Path) -> None:
    async with StateStore(db_path) as state:
        run_id = await state.begin_run()
        await state.record_submission(
            url="https://pending.example/",
            outcome="queued",
            run_id=run_id,
            last_visit_at=T0,
            page_id="pg_pending",
            server_status="queued",
        )


async def test_sweep_refreshes_pending_via_batch(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state.sqlite3"
    _write_config(config_path, db_path)
    await _seed_pending(db_path)

    httpx2_mock.add_response(
        method="GET",
        url=f"{BASE}/readyz",
        status_code=200,
        json=_READYZ_WITH_BATCH,
    )
    httpx2_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/pages/status/batch",
        status_code=200,
        json={
            "results": [
                {
                    "found": True,
                    "page_id": "pg_pending",
                    "status": "indexed",
                    "last_error": None,
                }
            ]
        },
    )

    await status_sweep._sweep(config_path, _console())  # noqa: SLF001

    async with StateStore(db_path) as state:
        counts = await state.status_counts()
        remaining = await state.nonterminal_pages(limit=10)
    assert counts.get("indexed") == 1
    assert remaining == []


async def test_sweep_requires_batch_capability(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    db_path = tmp_path / "state.sqlite3"
    _write_config(config_path, db_path)
    await _seed_pending(db_path)

    httpx2_mock.add_response(
        method="GET",
        url=f"{BASE}/readyz",
        status_code=200,
        json={"status": "ready"},  # a pre-0.2.0 server advertises no capabilities
        is_reusable=True,
    )

    with pytest.raises(RequiresBatchApiError):
        await status_sweep._sweep(config_path, _console())  # noqa: SLF001
