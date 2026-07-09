"""RefinderyClient behavior against a mocked API."""

import httpx
import pytest
import respx

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


@respx.mock(base_url=BASE)
async def test_ingest_accepted(respx_mock):
    route = respx_mock.post("/v1/pages").respond(
        202, json={"page_id": "pg_1", "status": "queued"}
    )
    async with make_client() as client:
        outcome = await client.ingest_url(request_for("https://a.example/"))
    assert outcome == Accepted(page_id="pg_1")
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer tok"
    assert b'"body_extracted"' not in sent.content
    assert b'"title":"T"' in sent.content.replace(b" ", b"")


@respx.mock(base_url=BASE)
async def test_ingest_revisit(respx_mock):
    respx_mock.post("/v1/pages").respond(
        200,
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


@respx.mock(base_url=BASE)
async def test_ingest_blacklisted(respx_mock):
    respx_mock.post("/v1/pages").respond(
        403, json={"error": "blacklisted", "pattern": "a.example"}
    )
    async with make_client() as client:
        outcome = await client.ingest_url(request_for("https://a.example/"))
    assert outcome == Blacklisted(pattern="a.example")


@respx.mock(base_url=BASE)
async def test_ingest_auth_error(respx_mock):
    respx_mock.post("/v1/pages").respond(
        401, json={"detail": "missing or invalid bearer token"}
    )
    async with make_client() as client:
        with pytest.raises(AuthError):
            await client.ingest_url(request_for("https://a.example/"))


@respx.mock(base_url=BASE)
async def test_ingest_validation_error(respx_mock):
    respx_mock.post("/v1/pages").respond(422, json={"detail": "naive datetime"})
    async with make_client() as client:
        with pytest.raises(ValidationRejectedError, match="naive datetime"):
            await client.ingest_url(request_for("https://a.example/"))


@respx.mock(base_url=BASE)
async def test_ingest_server_error(respx_mock):
    respx_mock.post("/v1/pages").respond(503)
    async with make_client() as client:
        with pytest.raises(ServerError):
            await client.ingest_url(request_for("https://a.example/"))


@respx.mock(base_url=BASE)
async def test_ingest_timeout_propagates(respx_mock):
    respx_mock.post("/v1/pages").mock(side_effect=httpx.ConnectTimeout("boom"))
    async with make_client() as client:
        with pytest.raises(httpx.TransportError):
            await client.ingest_url(request_for("https://a.example/"))


@respx.mock(base_url=BASE)
async def test_wait_ready_ok(respx_mock):
    respx_mock.get("/readyz").respond(200, json={"status": "ready"})
    async with make_client() as client:
        await client.wait_ready()


@respx.mock(base_url=BASE)
async def test_wait_ready_times_out(respx_mock):
    respx_mock.get("/readyz").respond(503, json={"detail": "no active embedding model"})
    async with make_client(ready_timeout=0.05) as client:
        with pytest.raises(ReadyTimeoutError):
            await client.wait_ready(poll_interval=0.01)


@respx.mock(base_url=BASE)
async def test_pending_job_count(respx_mock):
    respx_mock.get("/v1/jobs").respond(
        200, json={"jobs": [{"status": "pending"}, {"status": "pending"}]}
    )
    async with make_client() as client:
        assert await client.pending_job_count(limit=10) == 2


@respx.mock(base_url=BASE)
async def test_page_status(respx_mock):
    respx_mock.get("/v1/pages/pg_1/status").respond(
        200,
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


@respx.mock(base_url=BASE)
async def test_forget_and_blacklist(respx_mock):
    respx_mock.post("/v1/forget").respond(
        200,
        json={
            "blacklist_id": "bl_1",
            "pattern": "a.example",
            "kind": "domain",
            "pages_purged": 3,
            "vector_deletes_queued": 3,
        },
    )
    respx_mock.get("/v1/blacklist").respond(
        200,
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
    respx_mock.delete("/v1/blacklist/bl_1").respond(204)
    async with make_client() as client:
        forgotten = await client.forget(domain="a.example", reason="test")
        assert forgotten.pages_purged == 3
        rules = await client.list_blacklist()
        assert rules.entries[0].id == "bl_1"
        await client.remove_blacklist("bl_1")
