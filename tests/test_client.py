"""RefinderyClient behavior against a mocked API."""

import httpx2
import pytest
from pytest_httpx2 import HTTPXMock

from browser_history_refindery.api_client import (
    Accepted,
    AuthError,
    Blacklisted,
    ReadyTimeoutError,
    RefinderyClient,
    Revisit,
    ServerError,
    ValidationRejectedError,
)
from browser_history_refindery.api_models import IngestPageRequest

BASE = "http://testserver"


def endpoint(path: str) -> str:
    return f"{BASE}{path}"


def make_client(ready_timeout: float = 0.2) -> RefinderyClient:
    return RefinderyClient(
        base_url=BASE,
        auth_token="tok",
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


async def test_ingest_accepted(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=202,
        json={"page_id": "pg_1", "status": "queued"},
    )
    async with make_client() as client:
        outcome = await client.ingest_url(request_for("https://a.example/"))
    assert outcome == Accepted(page_id="pg_1")
    sent = httpx2_mock.get_request(method="POST", url=endpoint("/v1/pages"))
    assert sent is not None
    assert sent.headers["Authorization"] == "Bearer tok"
    assert b'"body_extracted"' not in sent.content
    assert b'"title":"T"' in sent.content.replace(b" ", b"")


async def test_ingest_revisit(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=200,
        json={
            "page_id": "pg_1",
            "status": "indexed",
            "revisit": True,
            "content_hash_differs": True,
        },
    )
    async with make_client() as client:
        outcome = await client.ingest_url(request_for("https://a.example/"))
    assert outcome == Revisit(
        page_id="pg_1", status="indexed", content_hash_differs=True
    )


async def test_ingest_blacklisted(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=403,
        json={"error": "blacklisted", "pattern": "a.example"},
    )
    async with make_client() as client:
        outcome = await client.ingest_url(request_for("https://a.example/"))
    assert outcome == Blacklisted(pattern="a.example")


async def test_ingest_auth_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=401,
        json={"detail": "missing or invalid bearer token"},
    )
    async with make_client() as client:
        with pytest.raises(AuthError):
            await client.ingest_url(request_for("https://a.example/"))


async def test_ingest_validation_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="POST",
        url=endpoint("/v1/pages"),
        status_code=422,
        json={"detail": "naive datetime"},
    )
    async with make_client() as client:
        with pytest.raises(ValidationRejectedError, match="naive datetime"):
            await client.ingest_url(request_for("https://a.example/"))


async def test_ingest_server_error(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(method="POST", url=endpoint("/v1/pages"), status_code=503)
    async with make_client() as client:
        with pytest.raises(ServerError):
            await client.ingest_url(request_for("https://a.example/"))


async def test_ingest_timeout_propagates(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_exception(
        exception=httpx2.ConnectTimeout("boom"),
        method="POST",
        url=endpoint("/v1/pages"),
    )
    async with make_client() as client:
        with pytest.raises(httpx2.TransportError):
            await client.ingest_url(request_for("https://a.example/"))


async def test_wait_ready_ok(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=200,
        json={"status": "ready"},
    )
    async with make_client() as client:
        await client.wait_ready()


async def test_wait_ready_times_out(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/readyz"),
        status_code=503,
        json={"detail": "no active embedding model"},
        is_reusable=True,
    )
    async with make_client(ready_timeout=0.05) as client:
        with pytest.raises(ReadyTimeoutError):
            await client.wait_ready(poll_interval=0.01)


async def test_pending_job_count(httpx2_mock: HTTPXMock) -> None:
    def check_request(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/jobs"
        assert dict(request.url.params) == {"status_filter": "pending", "limit": "10"}
        return httpx2.Response(
            status_code=200,
            json={"jobs": [{"status": "pending"}, {"status": "pending"}]},
        )

    httpx2_mock.add_callback(
        check_request,
        method="GET",
    )
    async with make_client() as client:
        assert await client.pending_job_count(limit=10) == 2


async def test_page_status(httpx2_mock: HTTPXMock) -> None:
    httpx2_mock.add_response(
        method="GET",
        url=endpoint("/v1/pages/pg_1/status"),
        status_code=200,
        json={
            "page_id": "pg_1",
            "status": "indexed",
            "last_error": None,
            "features": {"entities": {"status": "done", "last_error": None}},
        },
    )
    async with make_client() as client:
        status = await client.page_status("pg_1")
    assert status.status == "indexed"
    assert status.features["entities"].status == "done"


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
