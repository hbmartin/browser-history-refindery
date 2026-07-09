"""Pydantic models for the Refindery ingest API."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

PageStatus = Literal["queued", "indexing", "indexed", "failed", "dead"]

TERMINAL_PAGE_STATUSES: frozenset[str] = frozenset({"indexed", "dead"})


class IngestPageRequest(BaseModel):
    """Body for ``POST /v1/pages`` (URL-only mode: no body fields ever sent)."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    source: str
    fetched_at: AwareDatetime
    metadata: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        """JSON-safe request body without null fields."""
        return self.model_dump(mode="json", exclude_none=True)


class IngestAcceptedResponse(BaseModel):
    """202: a previously-unseen URL was queued for indexing."""

    page_id: str
    status: str


class IngestRevisitResponse(BaseModel):
    """200: the canonical URL was already known; a revisit was recorded."""

    page_id: str
    status: str
    revisit: bool
    content_hash_differs: bool = False


class BlacklistedResponse(BaseModel):
    """403: the URL or its domain matches a server-side blacklist rule."""

    error: str
    pattern: str


class FeatureStatus(BaseModel):
    """Status of one asynchronous enrichment (e.g. entity extraction)."""

    status: str
    last_error: str | None = None


class PageStatusResponse(BaseModel):
    """Response for ``GET /v1/pages/{id}/status``."""

    page_id: str
    status: PageStatus
    last_error: str | None = None
    features: dict[str, FeatureStatus] = Field(default_factory=dict)


class ForgetResponse(BaseModel):
    """Response for ``POST /v1/forget``."""

    blacklist_id: str
    pattern: str
    kind: str
    pages_purged: int
    vector_deletes_queued: int


class BlacklistEntry(BaseModel):
    """One server-side blacklist rule."""

    id: str
    pattern: str
    kind: str
    reason: str | None = None
    created_at: str


class BlacklistResponse(BaseModel):
    """Response for ``GET /v1/blacklist``."""

    entries: list[BlacklistEntry]
