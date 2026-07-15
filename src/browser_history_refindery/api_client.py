"""Async HTTP client for the Refindery ingest API."""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from http import HTTPStatus
from types import TracebackType
from typing import Any, Self

import httpx2

from browser_history_refindery.api_models import (
    BlacklistResponse,
    EstimateBatchRequest,
    EstimateBatchResponse,
    ForgetResponse,
    IngestBatchAcceptedResult,
    IngestBatchBlacklistedResult,
    IngestBatchRejectedResult,
    IngestBatchRequest,
    IngestBatchResponse,
    IngestBatchResult,
    IngestBatchRevisitResult,
    IngestPageRequest,
    PageStatusBatchRequest,
    PageStatusBatchResponse,
    PageStatusBatchResult,
    ReadyzResponse,
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


@dataclass(frozen=True, slots=True)
class Rejected:
    """422-equivalent: one batch item was independently invalid."""

    detail: str


IngestOutcome = Accepted | Revisit | Blacklisted
BatchOutcome = Accepted | Revisit | Blacklisted | Rejected


@dataclass(frozen=True, slots=True)
class BatchItemOutcome:
    """One page's outcome within a batch, tagged with its input index."""

    index: int
    outcome: BatchOutcome


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


class RequiresBatchApiError(RuntimeError):
    """The server does not advertise a batch capability this tool requires."""

    def __init__(self, capability: str) -> None:
        super().__init__(
            f"the Refindery server does not advertise the {capability!r} capability: "
            "this tool requires Refindery >= 0.2.0 (check that GET /readyz reports it)"
        )


class InvalidEstimateResponseError(RuntimeError):
    """An estimate response did not map exactly once to every request item."""

    def __init__(self, *, expected: int, indices: list[int]) -> None:
        super().__init__(
            f"estimate response indices must be exactly 0..{expected - 1}; "
            f"got {indices}"
        )


class RefinderyClient:
    """Thin typed wrapper over the ingest and lifecycle endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str | None,
        request_timeout: float,
        ready_timeout: float,
    ) -> None:
        self._ready_timeout = ready_timeout
        self._capabilities: frozenset[str] = frozenset()
        headers = (
            {} if auth_token is None else {"Authorization": f"Bearer {auth_token}"}
        )
        self._http = httpx2.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=request_timeout,
        )

    @property
    def supports_batch_ingest(self) -> bool:
        """Whether ``GET /readyz`` advertised the batch-ingest capability."""
        return "batch_ingest" in self._capabilities

    @property
    def supports_batch_status(self) -> bool:
        """Whether ``GET /readyz`` advertised the batch-status capability."""
        return "batch_status" in self._capabilities

    @property
    def supports_batch_estimate(self) -> bool:
        """Whether ``GET /readyz`` advertised live batch estimation."""
        return "batch_estimate" in self._capabilities

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
        """Block until ``GET /readyz`` reports ready, recording capabilities."""
        deadline = time.monotonic() + self._ready_timeout
        while True:
            if await self.probe_ready():
                return
            if time.monotonic() >= deadline:
                raise ReadyTimeoutError(self._ready_timeout)
            await asyncio.sleep(poll_interval)

    async def probe_ready(self) -> bool:
        """Probe readiness once and record advertised capabilities."""
        response = None
        # A backend that has not bound its socket yet is simply not ready.
        with contextlib.suppress(httpx2.TransportError):
            response = await self._http.get("/readyz")
        if response is None or response.status_code != HTTPStatus.OK:
            self._capabilities = frozenset()
            return False
        self._capabilities = self._parse_capabilities(response)
        return True

    async def ingest_batch(
        self, pages: list[IngestPageRequest]
    ) -> list[BatchItemOutcome]:
        """POST up to 100 pages and return each item's outcome by input index."""
        request = IngestBatchRequest(pages=pages)
        response = await self._http.post("/v1/pages/batch", json=request.payload())
        match response.status_code:
            case HTTPStatus.OK:
                parsed = IngestBatchResponse.model_validate(response.json())
                return [self._batch_outcome(result) for result in parsed.results]
            case HTTPStatus.UNAUTHORIZED:
                raise AuthError
            case HTTPStatus.UNPROCESSABLE_CONTENT:
                raise ValidationRejectedError(self._detail(response))
            case status:
                raise ServerError(status)

    async def estimate_batch(
        self, pages: list[IngestPageRequest]
    ) -> EstimateBatchResponse:
        """Estimate up to 100 pages without submitting or indexing them."""
        request = EstimateBatchRequest(pages=pages)
        response = await self._http.post(
            "/v1/pages/estimate/batch", json=request.payload()
        )
        match response.status_code:
            case HTTPStatus.OK:
                parsed = EstimateBatchResponse.model_validate(response.json())
                indices = [result.index for result in parsed.results]
                expected = len(pages)
                if sorted(indices) != list(range(expected)):
                    raise InvalidEstimateResponseError(
                        expected=expected, indices=indices
                    )
                return parsed
            case HTTPStatus.UNAUTHORIZED:
                raise AuthError
            case HTTPStatus.UNPROCESSABLE_CONTENT:
                raise ValidationRejectedError(self._detail(response))
            case status:
                raise ServerError(status)

    async def page_status_batch(
        self, page_ids: list[str]
    ) -> list[PageStatusBatchResult]:
        """Fetch the lifecycle status of up to 500 pages in one request."""
        request = PageStatusBatchRequest(page_ids=page_ids)
        response = await self._http.post(
            "/v1/pages/status/batch", json=request.model_dump()
        )
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise AuthError
        response.raise_for_status()
        return PageStatusBatchResponse.model_validate(response.json()).results

    async def pending_job_count(self, *, limit: int) -> int:
        """Estimate the server's pending-job backlog (capped at ``limit``)."""
        response = await self._http.get(
            "/v1/jobs", params={"status": "pending", "limit": limit}
        )
        response.raise_for_status()
        return _count_rows(response.json())

    @staticmethod
    def _batch_outcome(result: IngestBatchResult) -> BatchItemOutcome:
        match result:
            case IngestBatchAcceptedResult(index=index, page_id=page_id):
                return BatchItemOutcome(index, Accepted(page_id=page_id))
            case IngestBatchRevisitResult(
                index=index,
                page_id=page_id,
                status=status,
                content_hash_differs=differs,
            ):
                return BatchItemOutcome(
                    index,
                    Revisit(
                        page_id=page_id, status=status, content_hash_differs=differs
                    ),
                )
            case IngestBatchBlacklistedResult(index=index, pattern=pattern):
                return BatchItemOutcome(index, Blacklisted(pattern=pattern))
            case IngestBatchRejectedResult(index=index, detail=detail):
                return BatchItemOutcome(index, Rejected(detail=detail))

    @staticmethod
    def _parse_capabilities(response: httpx2.Response) -> frozenset[str]:
        try:
            parsed = ReadyzResponse.model_validate(response.json())
        except (ValueError, TypeError):
            return frozenset()
        return frozenset(name for name, on in parsed.capabilities.items() if on)

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
