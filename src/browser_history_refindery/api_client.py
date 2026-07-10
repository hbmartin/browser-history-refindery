"""Async HTTP client for the Refindery ingest API."""

import asyncio
import time
from dataclasses import dataclass
from http import HTTPStatus
from types import TracebackType
from typing import Any, Self

import httpx2

from browser_history_refindery.api_models import (
    BlacklistedResponse,
    BlacklistResponse,
    ForgetResponse,
    IngestAcceptedResponse,
    IngestPageRequest,
    IngestRevisitResponse,
    PageStatusResponse,
)


@dataclass(frozen=True, slots=True)
class Accepted:
    """202: new page queued for indexing."""

    page_id: str


@dataclass(frozen=True, slots=True)
class Revisit:
    """200: known page; server recorded a revisit."""

    page_id: str
    status: str
    content_hash_differs: bool


@dataclass(frozen=True, slots=True)
class Blacklisted:
    """403: server-side blacklist rule matched; not ingested."""

    pattern: str


IngestOutcome = Accepted | Revisit | Blacklisted


class AuthError(RuntimeError):
    """The server rejected our bearer token."""

    def __init__(self) -> None:
        super().__init__(
            "the server rejected the bearer token (401): check server.auth_token "
            "in config.toml or the REFINDERY_AUTH_TOKEN environment variable"
        )


class ValidationRejectedError(RuntimeError):
    """The server rejected a request body as invalid (422)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"server rejected the request: {detail}")


class ServerError(RuntimeError):
    """An unexpected (usually 5xx) response worth retrying."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"unexpected server response: HTTP {status_code}")
        self.status_code = status_code


class ReadyTimeoutError(RuntimeError):
    """The server never reported ready within the configured window."""

    def __init__(self, waited: float) -> None:
        super().__init__(
            f"server not ready after {waited:.0f}s: is Refindery running and is "
            "GET /readyz returning 200?"
        )


class RefinderyClient:
    """Thin typed wrapper over the ingest and lifecycle endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        request_timeout: float,
        ready_timeout: float,
    ) -> None:
        self._ready_timeout = ready_timeout
        self._http = httpx2.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=request_timeout,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def wait_ready(self, *, poll_interval: float = 1.0) -> None:
        """Block until ``GET /readyz`` reports ready, or raise on timeout."""
        deadline = time.monotonic() + self._ready_timeout
        while True:
            try:
                response = await self._http.get("/readyz")
                if response.status_code == HTTPStatus.OK:
                    return
            except httpx2.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise ReadyTimeoutError(self._ready_timeout)
            await asyncio.sleep(poll_interval)

    async def ingest_url(self, request: IngestPageRequest) -> IngestOutcome:
        """POST one page and interpret the three expected outcomes."""
        response = await self._http.post("/v1/pages", json=request.payload())
        match response.status_code:
            case HTTPStatus.ACCEPTED:
                accepted = IngestAcceptedResponse.model_validate(response.json())
                return Accepted(page_id=accepted.page_id)
            case HTTPStatus.OK:
                revisit = IngestRevisitResponse.model_validate(response.json())
                return Revisit(
                    page_id=revisit.page_id,
                    status=revisit.status,
                    content_hash_differs=revisit.content_hash_differs,
                )
            case HTTPStatus.FORBIDDEN:
                rejected = BlacklistedResponse.model_validate(response.json())
                return Blacklisted(pattern=rejected.pattern)
            case HTTPStatus.UNAUTHORIZED:
                raise AuthError
            case HTTPStatus.UNPROCESSABLE_CONTENT:
                raise ValidationRejectedError(self._detail(response))
            case status:
                raise ServerError(status)

    async def page_status(self, page_id: str) -> PageStatusResponse:
        """Fetch the lifecycle status of one page."""
        response = await self._http.get(f"/v1/pages/{page_id}/status")
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise AuthError
        response.raise_for_status()
        return PageStatusResponse.model_validate(response.json())

    async def pending_job_count(self, *, limit: int) -> int:
        """Estimate the server's pending-job backlog (capped at ``limit``)."""
        response = await self._http.get(
            "/v1/jobs", params={"status_filter": "pending", "limit": limit}
        )
        response.raise_for_status()
        return _count_rows(response.json())

    async def forget(
        self,
        *,
        url: str | None = None,
        domain: str | None = None,
        reason: str | None = None,
    ) -> ForgetResponse:
        """Purge and blacklist a URL or domain. Destructive."""
        body: dict[str, str] = {}
        if url is not None:
            body["url"] = url
        if domain is not None:
            body["domain"] = domain
        if reason is not None:
            body["reason"] = reason
        response = await self._http.post("/v1/forget", json=body)
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise AuthError
        if response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT:
            raise ValidationRejectedError(self._detail(response))
        response.raise_for_status()
        return ForgetResponse.model_validate(response.json())

    async def list_blacklist(self) -> BlacklistResponse:
        """List all server-side blacklist rules."""
        response = await self._http.get("/v1/blacklist")
        response.raise_for_status()
        return BlacklistResponse.model_validate(response.json())

    async def remove_blacklist(self, blacklist_id: str) -> None:
        """Delete one blacklist rule (purged content stays purged)."""
        response = await self._http.delete(f"/v1/blacklist/{blacklist_id}")
        response.raise_for_status()

    @staticmethod
    def _detail(response: httpx2.Response) -> str:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        return str(detail) if detail else response.text[:200]


def _count_rows(payload: Any) -> int:  # noqa: ANN401 - shape intentionally unknown
    """Count job rows in a response whose exact envelope shape may vary."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return len(value)
    return 0
