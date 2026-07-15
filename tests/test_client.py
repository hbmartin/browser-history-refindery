"""RefinderyClient behavior against a mocked API."""

import re
from decimal import Decimal

import httpx2
import pytest
from pytest_httpx2 import HTTPXMock

from browser_history_refindery.api_client import (
    Accepted,
    AuthError,
    BatchItemOutcome,
    Blacklisted,
    InvalidEstimateResponseError,
    ReadyTimeoutError,
    RefinderyClient,
    Rejected,
    Revisit,
    ServerError,
    ValidationRejectedError,
)
from browser_history_refindery.api_models import (
    EstimateBatchBlacklistedResult,
    EstimateBatchEstimatedResult,
    EstimateBatchRejectedResult,
    EstimateBatchRevisitResult,
    EstimateBatchUnavailableResult,
    IngestPageRequest,
    PageStatusBatchFoundResult,
    PageStatusBatchMissingResult,
)

BASE = "http://testserver"


def endpoint(path: str) -> str:
    return f"{BASE}{path}"


def make_client(
    ready_timeout: float = 0.2, *, with_auth: bool = True
) -> RefinderyClient:
    return RefinderyClient(
        base_url=BASE,
        auth_token="tok" if with_auth else None,
        request_timeout=1.0,
        ready_timeout=ready_timeout,
    )


def request_for(url: str) -> IngestPageRequest:
    return IngestPageRequest(
        url=url,
        title="T",
        source="history-import:chrome",
        fetched_at="2026-07-09T12:00:00Z",
        metadata={"browser": "chrome"},
    )


def estimate_profile_payload() -> dict[str, object]:
    return {
        "config_fingerprint": "cfg-1",
        "generated_at": "2026-07-14T12:00:00Z",
        "storage_bytes_per_page": 4_096,
        "cost_usd_per_page": "0.0004",
        "cost_breakdown_usd_per_page": {"embedding": "0.0004"},
        "unpriced_components": [],
    }


async def test_ingest_batch_maps_mixed_outcomes(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/batch"),
        status_code=200,
        json={
            "results": [
                {
                    "outcome": "accepted",
                    "index": 0,
                    "page_id": "pg_1",
                    "status": "queued",
                },
                {
                    "outcome": "revisit",
                    "index": 1,
                    "page_id": "pg_2",
                    "status": "indexed",
                    "content_hash_differs": True,
                },
                {"outcome": "blacklisted", "index": 2, "pattern": "b.example"},
                {"outcome": "rejected", "index": 3, "detail": "naive datetime"},
            ]
        },
    )
    async with make_client() as client:
        outcomes = await client.ingest_batch(
            [
                request_for("https://a.example/"),
                request_for("https://revisit.example/"),
                request_for("https://b.example/"),
                request_for("https://bad.example/"),
            ]
        )
    assert outcomes == [
        BatchItemOutcome(0, Accepted(page_id="pg_1")),
        BatchItemOutcome(
            1, Revisit(page_id="pg_2", status="indexed", content_hash_differs=True)
        ),
        BatchItemOutcome(2, Blacklisted(pattern="b.example")),
        BatchItemOutcome(3, Rejected(detail="naive datetime")),
    ]
    sent = httpx2_mock.get_request(method="POST", url=endpoint("/v1/pages/batch"))
    assert sent is not None
    assert sent.headers["Authorization"] == "Bearer tok"
    assert b'"body_extracted"' not in sent.content
    assert b'"pages":[' in sent.content.replace(b" ", b"")


async def test_ingest_batch_auth_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/batch"),
        status_code=401,
        json={"detail": "missing or invalid bearer token"},
    )
    async with make_client() as client:
        with pytest.raises(AuthError):
            await client.ingest_batch([request_for("https://a.example/")])


async def test_ingest_batch_envelope_validation_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/batch"),
        status_code=422,
        json={"detail": "too many pages"},
    )
    async with make_client() as client:
        with pytest.raises(ValidationRejectedError, match="too many pages"):
            await client.ingest_batch([request_for("https://a.example/")])


async def test_ingest_batch_server_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST", url=endpoint("/v1/pages/batch"), status_code=503
    )
    async with make_client() as client:
        with pytest.raises(ServerError):
            await client.ingest_batch([request_for("https://a.example/")])


async def test_ingest_batch_timeout_propagates(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_exception(
        exception=httpx2.ConnectTimeout("boom"),
        method="POST",
        url=endpoint("/v1/pages/batch"),
    )
    async with make_client() as client:
        with pytest.raises(httpx2.TransportError):
            await client.ingest_batch([request_for("https://a.example/")])


async def test_estimate_batch_maps_every_outcome(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": estimate_profile_payload(),
            "results": [
                {
                    "outcome": "estimated",
                    "index": 0,
                    "token_count": 600,
                    "chunk_count": 2,
                    "estimated_storage_bytes": 5_000,
                    "estimated_cost_usd": "0.0006",
                    "cost_breakdown_usd": {"embedding": "0.0006"},
                    "unpriced_components": [],
                },
                {"outcome": "revisit", "index": 1},
                {"outcome": "blacklisted", "index": 2, "pattern": "blocked"},
                {"outcome": "rejected", "index": 3, "detail": "bad URL"},
                {"outcome": "unavailable", "index": 4, "detail": "timeout"},
            ],
        },
    )
    pages = [request_for(f"https://{index}.example/") for index in range(5)]
    async with make_client() as client:
        response = await client.estimate_batch(pages)

    assert isinstance(response.results[0], EstimateBatchEstimatedResult)
    assert response.results[0].estimated_cost_usd == Decimal("0.0006")
    assert isinstance(response.results[1], EstimateBatchRevisitResult)
    assert isinstance(response.results[2], EstimateBatchBlacklistedResult)
    assert isinstance(response.results[3], EstimateBatchRejectedResult)
    assert isinstance(response.results[4], EstimateBatchUnavailableResult)
    request = httpx2_mock.get_request(
        method="POST", url=endpoint("/v1/pages/estimate/batch")
    )
    assert request is not None
    assert request.headers["Authorization"] == "Bearer tok"
    assert b'"metadata"' in request.content
    assert b'"body_extracted"' not in request.content


@pytest.mark.parametrize("indices", [[0, 0], [0], [0, 2]])
async def test_estimate_batch_rejects_invalid_index_mapping(
    httpx2_mock: HTTPXMock, indices: list[int]
) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": estimate_profile_payload(),
            "results": [{"outcome": "revisit", "index": index} for index in indices],
        },
    )
    async with make_client() as client:
        with pytest.raises(InvalidEstimateResponseError):
            await client.estimate_batch(
                [request_for("https://a.example/"), request_for("https://b.example/")]
            )


async def test_estimate_batch_auth_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=401,
    )
    async with make_client(with_auth=False) as client:
        with pytest.raises(AuthError):
            await client.estimate_batch([request_for("https://a.example/")])


async def test_estimate_batch_rejects_incomplete_cost_contract(
    httpx2_mock: HTTPXMock,
) -> None:
    malformed = estimate_profile_payload()
    malformed["cost_usd_per_page"] = None
    malformed["unpriced_components"] = []
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/estimate/batch"),
        status_code=200,
        json={
            "profile": malformed,
            "results": [{"outcome": "revisit", "index": 0}],
        },
    )
    async with make_client() as client:
        with pytest.raises(ValueError, match="unpriced component"):
            await client.estimate_batch([request_for("https://a.example/")])


async def test_wait_ready_records_capabilities(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=200,
        json={
            "status": "ready",
            "capabilities": {
                "batch_ingest": True,
                "batch_status": True,
                "batch_estimate": True,
            },
        },
    )
    async with make_client() as client:
        await client.wait_ready()
        assert client.supports_batch_ingest is True
        assert client.supports_batch_status is True
        assert client.supports_batch_estimate is True


async def test_wait_ready_without_capabilities_is_unsupported(
    httpx2_mock: HTTPXMock,
) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=200,
        json={"status": "ready"},
    )
    async with make_client() as client:
        await client.wait_ready()
        assert client.supports_batch_ingest is False
        assert client.supports_batch_status is False
        assert client.supports_batch_estimate is False


async def test_wait_ready_recovers_from_transport_error(
    httpx2_mock: HTTPXMock,
) -> None:
    httpx2_mock.add_exception(
        exception=httpx2.ConnectError("backend is starting"),
        method="GET",
        url=endpoint("/readyz"),
    )
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=200,
        json={
            "status": "ready",
            "capabilities": {"batch_ingest": True, "batch_status": True},
        },
    )
    async with make_client() as client:
        await client.wait_ready(poll_interval=0)
        assert client.supports_batch_ingest is True


async def test_wait_ready_times_out(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=503,
        json={"status": "no active embedding model"},
        is_reusable=True,
    )
    async with make_client(ready_timeout=0.05) as client:
        with pytest.raises(ReadyTimeoutError):
            await client.wait_ready(poll_interval=0.01)


async def test_pending_job_count(httpx2_mock: HTTPXMock) -> None:
    def check_request(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/jobs"
        assert dict(request.url.params) == {"status": "pending", "limit": "10"}
        return httpx2.Response(
            status_code=200,
            json={"jobs": [{"status": "pending"}, {"status": "pending"}]},
        )

    httpx2_mock.add_callback(
        check_request,
        url=re.compile(rf"^{re.escape(endpoint('/v1/jobs'))}(?:\?.*)?$"),
        method="GET",
    )
    async with make_client() as client:
        assert await client.pending_job_count(limit=10) == 2


async def test_page_status_batch(httpx2_mock: HTTPXMock) -> None:
    def check_request(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/pages/status/batch"
        return httpx2.Response(
            status_code=200,
            json={
                "results": [
                    {
                        "found": True,
                        "page_id": "pg_1",
                        "status": "indexed",
                        "last_error": None,
                    },
                    {"found": False, "page_id": "pg_missing"},
                ]
            },
        )

    httpx2_mock.add_callback(
        check_request,
        method="POST",
        url=endpoint("/v1/pages/status/batch"),
    )
    async with make_client() as client:
        results = await client.page_status_batch(["pg_1", "pg_missing"])
    assert isinstance(results[0], PageStatusBatchFoundResult)
    assert results[0].status == "indexed"
    assert isinstance(results[1], PageStatusBatchMissingResult)
    assert results[1].page_id == "pg_missing"


async def test_page_status_batch_auth_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages/status/batch"),
        status_code=401,
        json={"detail": "missing or invalid bearer token"},
    )
    async with make_client() as client:
        with pytest.raises(AuthError):
            await client.page_status_batch(["pg_1"])


async def test_forget_and_blacklist(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/forget"),
        status_code=200,
        json={
            "blacklist_id": "bl_1",
            "pattern": "a.example",
            "kind": "domain",
            "pages_purged": 3,
            "vector_deletes_queued": 3,
        },
    )
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/v1/blacklist"),
        status_code=200,
        json={
            "entries": [
                {
                    "id": "bl_1",
                    "pattern": "a.example",
                    "kind": "domain",
                    "reason": "test",
                    "created_at": "2026-07-09T12:00:00Z",
                }
            ]
        },
    )
    httpx2_mock.add_response(
        method="DELETE",
        url=endpoint("/v1/blacklist/bl_1"),
        status_code=204,
    )
    async with make_client() as client:
        forgotten = await client.forget(domain="a.example", reason="test")
        assert forgotten.pages_purged == 3
        rules = await client.list_blacklist()
        assert rules.entries[0].id == "bl_1"
        await client.remove_blacklist("bl_1")
