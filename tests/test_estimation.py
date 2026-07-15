"""Configuration-aware dry-run estimate aggregation."""

from decimal import Decimal
from pathlib import Path

import httpx2
from pytest_httpx2 import HTTPXMock

from browser_history_refindery.api_models import (
    EstimateFallbackProfile,
    IngestPageRequest,
)
from browser_history_refindery.config import AppConfig
from browser_history_refindery.estimation import estimate_pages, refindery_domain
from browser_history_refindery.state import StateStore

BASE = "http://testserver"


def endpoint(path: str) -> str:
    return f"{BASE}{path}"


def make_config(tmp_path: Path, *, with_auth: bool = True) -> AppConfig:
    return AppConfig.model_validate(
        {
            "server": {
                "base_url": BASE,
                "auth_token": "tok" if with_auth else None,
            },
            "state": {"db_path": str(tmp_path / "state.sqlite3")},
        }
    )


def page(url: str) -> IngestPageRequest:
    return IngestPageRequest(
        url=url,
        title="Title",
        source="history-import:chrome",
        fetched_at="2026-07-14T12:00:00Z",
        metadata={"browser": "chrome"},
    )


def profile_payload(
    *, cost: str | None = "0.001", unpriced: list[str] | None = None
) -> dict[str, object]:
    return {
        "config_fingerprint": "cfg-1",
        "generated_at": "2026-07-14T12:00:00Z",
        "storage_bytes_per_page": 4_096,
        "cost_usd_per_page": cost,
        "cost_breakdown_usd_per_page": (
            {"embedding": cost} if cost is not None else {}
        ),
        "unpriced_components": unpriced or [],
    }


def ready(httpx2_mock: HTTPXMock, *, estimate: bool = True) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=200,
        json={
            "status": "ready",
            "capabilities": {"batch_estimate": estimate},
        },
    )


def test_refindery_domain_normalization() -> None:
    assert refindery_domain("https://WWW.Example.com/path") == "example.com"
    assert refindery_domain("https://docs.example.com/path") == "docs.example.com"
    assert refindery_domain("https:///missing-host") == "(unknown)"


async def test_live_estimate_aggregates_every_outcome(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    ready(httpx2_mock)
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": profile_payload(),
            "results": [
                {
                    "outcome": "estimated",
                    "index": 0,
                    "token_count": 500,
                    "chunk_count": 2,
                    "estimated_storage_bytes": 1_000,
                    "estimated_cost_usd": "0.002",
                    "cost_breakdown_usd": {"embedding": "0.002"},
                    "unpriced_components": [],
                },
                {"outcome": "unavailable", "index": 1, "detail": "timeout"},
                {"outcome": "revisit", "index": 2},
                {"outcome": "blacklisted", "index": 3, "pattern": "blocked"},
                {"outcome": "rejected", "index": 4, "detail": "bad URL"},
            ],
        },
    )
    pages = [
        page("https://www.example.com/a"),
        page("https://example.com/b"),
        page("https://docs.example.com/c"),
        page("https://blocked.example/d"),
        page("https://other.example/e"),
    ]
    config = make_config(tmp_path)
    async with StateStore(config.state.db_path) as state:
        estimate = await estimate_pages(
            pages, config=config, state=state, batch_size=100
        )
        cached = await state.get_estimation_profile(server_base_url=BASE)

    assert estimate.total_pages == 5
    assert estimate.domains == (
        ("example.com", 2),
        ("blocked.example", 1),
        ("docs.example.com", 1),
        ("other.example", 1),
    )
    assert estimate.live_pages == 1
    assert estimate.fallback_pages == 1
    assert estimate.zero_impact_pages == 3
    assert estimate.unavailable_pages == 0
    assert estimate.storage_bytes == 5_096
    assert estimate.cost_usd == Decimal("0.003")
    assert estimate.cost_breakdown_usd == {"embedding": Decimal("0.003")}
    assert cached is not None
    assert cached.config_fingerprint == "cfg-1"


async def test_cached_profile_handles_missing_capability(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    ready(httpx2_mock, estimate=False)
    config = make_config(tmp_path)
    cached = EstimateFallbackProfile.model_validate(profile_payload())
    async with StateStore(config.state.db_path) as state:
        await state.set_estimation_profile(server_base_url=BASE, profile=cached)
        estimate = await estimate_pages(
            [page("https://a.example"), page("https://b.example")],
            config=config,
            state=state,
            batch_size=100,
        )

    assert estimate.live_pages == 0
    assert estimate.fallback_pages == 2
    assert estimate.storage_bytes == 8_192
    assert estimate.cost_usd == Decimal("0.002")
    assert any("batch_estimate" in note for note in estimate.notes)


async def test_missing_token_uses_cached_profile(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    ready(httpx2_mock)
    config = make_config(tmp_path, with_auth=False)
    cached = EstimateFallbackProfile.model_validate(profile_payload())
    async with StateStore(config.state.db_path) as state:
        await state.set_estimation_profile(server_base_url=BASE, profile=cached)
        estimate = await estimate_pages(
            [page("https://a.example")],
            config=config,
            state=state,
            batch_size=100,
        )

    assert estimate.fallback_pages == 1
    assert estimate.storage_bytes == 4_096
    assert any("auth token" in note for note in estimate.notes)
    assert (
        httpx2_mock.get_requests(
            method="POST", url=endpoint("/v1/pages/estimate/batch")
        )
        == []
    )


async def test_fresh_cache_reports_unavailable_when_refindery_is_down(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx2_mock.add_exception(
        exception=httpx2.ConnectError("down"),
        method="GET",
        url=endpoint("/readyz"),
    )
    config = make_config(tmp_path)
    async with StateStore(config.state.db_path) as state:
        estimate = await estimate_pages(
            [page("https://a.example"), page("https://b.example")],
            config=config,
            state=state,
            batch_size=100,
        )

    assert estimate.storage_bytes is None
    assert estimate.cost_usd is None
    assert estimate.unavailable_pages == 2


async def test_partial_batch_failure_uses_new_profile_only_for_unresolved_page(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    ready(httpx2_mock)
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": profile_payload(),
            "results": [
                {
                    "outcome": "estimated",
                    "index": 0,
                    "token_count": 100,
                    "chunk_count": 1,
                    "estimated_storage_bytes": 1_000,
                    "estimated_cost_usd": "0.002",
                    "cost_breakdown_usd": {"embedding": "0.002"},
                    "unpriced_components": [],
                }
            ],
        },
    )
    httpx2_mock.add_exception(
        exception=httpx2.ReadTimeout("timeout"),
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
    )
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": profile_payload(),
            "results": [{"outcome": "revisit", "index": 0}],
        },
    )
    config = make_config(tmp_path)
    async with StateStore(config.state.db_path) as state:
        estimate = await estimate_pages(
            [
                page("https://a.example"),
                page("https://b.example"),
                page("https://c.example"),
            ],
            config=config,
            state=state,
            batch_size=1,
        )

    assert estimate.live_pages == 1
    assert estimate.fallback_pages == 1
    assert estimate.zero_impact_pages == 1
    assert estimate.storage_bytes == 5_096
    assert estimate.cost_usd == Decimal("0.003")


async def test_unpriced_component_makes_total_cost_unavailable(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    ready(httpx2_mock)
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": profile_payload(cost=None, unpriced=["entity-llm"]),
            "results": [
                {
                    "outcome": "estimated",
                    "index": 0,
                    "token_count": 100,
                    "chunk_count": 1,
                    "estimated_storage_bytes": 1_000,
                    "estimated_cost_usd": None,
                    "cost_breakdown_usd": {},
                    "unpriced_components": ["entity-llm"],
                }
            ],
        },
    )
    config = make_config(tmp_path)
    async with StateStore(config.state.db_path) as state:
        estimate = await estimate_pages(
            [page("https://a.example")],
            config=config,
            state=state,
            batch_size=100,
        )

    assert estimate.storage_bytes == 1_000
    assert estimate.cost_usd is None
    assert estimate.unpriced_components == ("entity-llm",)


async def test_zero_pages_skips_refindery_probe(
    httpx2_mock: HTTPXMock, tmp_path: Path
) -> None:
    config = make_config(tmp_path)
    async with StateStore(config.state.db_path) as state:
        estimate = await estimate_pages([], config=config, state=state, batch_size=100)
    assert estimate.total_pages == 0
    assert estimate.storage_bytes == 0
    assert estimate.cost_usd == 0
    assert httpx2_mock.get_requests() == []
