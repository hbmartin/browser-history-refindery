"""Pydantic models for the Refindery ingest API."""

from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

PageStatus = Literal["queued", "indexing", "indexed", "failed", "dead"]

TERMINAL_PAGE_STATUSES: frozenset[str] = frozenset({"indexed", "dead"})

# Refindery >= 0.2.0 batch limits (from the upstream ingest contract).
MAX_INGEST_BATCH = 100
MAX_STATUS_BATCH = 500

NonNegativeDecimal = Annotated[Decimal, Field(ge=Decimal(0), allow_inf_nan=False)]


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


class IngestBatchRequest(BaseModel):
    """Body for ``POST /v1/pages/batch`` (Refindery >= 0.2.0)."""

    model_config = ConfigDict(extra="forbid")

    pages: list[IngestPageRequest] = Field(min_length=1, max_length=MAX_INGEST_BATCH)

    def payload(self) -> dict[str, Any]:
        """JSON-safe envelope with each page dumped without null fields."""
        return {"pages": [page.payload() for page in self.pages]}


class IngestBatchAcceptedResult(BaseModel):
    """One newly-queued item in a batch response (mirrors 202)."""

    outcome: Literal["accepted"]
    index: int
    page_id: str
    status: str = "queued"


class IngestBatchRevisitResult(BaseModel):
    """One revisit item in a batch response (mirrors 200)."""

    outcome: Literal["revisit"]
    index: int
    page_id: str
    status: PageStatus
    content_hash_differs: bool = False


class IngestBatchBlacklistedResult(BaseModel):
    """One blacklisted item in a batch response (mirrors 403)."""

    outcome: Literal["blacklisted"]
    index: int
    pattern: str


class IngestBatchRejectedResult(BaseModel):
    """One independently-invalid item in a batch response (mirrors 422)."""

    outcome: Literal["rejected"]
    index: int
    detail: str


IngestBatchResult = Annotated[
    IngestBatchAcceptedResult
    | IngestBatchRevisitResult
    | IngestBatchBlacklistedResult
    | IngestBatchRejectedResult,
    Field(discriminator="outcome"),
]


class IngestBatchResponse(BaseModel):
    """Response for ``POST /v1/pages/batch`` (ordered per-item outcomes)."""

    results: list[IngestBatchResult]


class EstimateBatchRequest(BaseModel):
    """Body for ``POST /v1/pages/estimate/batch``."""

    model_config = ConfigDict(extra="forbid")

    pages: list[IngestPageRequest] = Field(min_length=1, max_length=MAX_INGEST_BATCH)

    def payload(self) -> dict[str, Any]:
        """JSON-safe envelope matching the real batch-ingest request."""
        return {"pages": [page.payload() for page in self.pages]}


class EstimateFallbackProfile(BaseModel):
    """Configuration-aware per-page fallback returned by Refindery."""

    model_config = ConfigDict(extra="forbid")

    config_fingerprint: str = Field(min_length=1)
    generated_at: AwareDatetime
    storage_bytes_per_page: int = Field(ge=0)
    cost_usd_per_page: NonNegativeDecimal | None
    cost_breakdown_usd_per_page: dict[str, NonNegativeDecimal] = Field(
        default_factory=dict
    )
    unpriced_components: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent_cost(self) -> Self:
        if self.cost_usd_per_page is None and not self.unpriced_components:
            msg = "a missing fallback cost requires at least one unpriced component"
            raise ValueError(msg)
        if self.cost_usd_per_page is not None and self.unpriced_components:
            msg = "a complete fallback cost cannot have unpriced components"
            raise ValueError(msg)
        return self


class EstimateBatchEstimatedResult(BaseModel):
    """A new page whose incremental resource use could be estimated."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["estimated"]
    index: int = Field(ge=0)
    token_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    estimated_storage_bytes: int = Field(ge=0)
    estimated_cost_usd: NonNegativeDecimal | None
    cost_breakdown_usd: dict[str, NonNegativeDecimal] = Field(default_factory=dict)
    unpriced_components: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent_cost(self) -> Self:
        if self.estimated_cost_usd is None and not self.unpriced_components:
            msg = "a missing estimate cost requires at least one unpriced component"
            raise ValueError(msg)
        if self.estimated_cost_usd is not None and self.unpriced_components:
            msg = "a complete estimate cost cannot have unpriced components"
            raise ValueError(msg)
        return self


class EstimateBatchRevisitResult(BaseModel):
    """A page already stored by Refindery, with zero incremental impact."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["revisit"]
    index: int = Field(ge=0)


class EstimateBatchBlacklistedResult(BaseModel):
    """A server-blacklisted page, with zero incremental impact."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["blacklisted"]
    index: int = Field(ge=0)
    pattern: str


class EstimateBatchRejectedResult(BaseModel):
    """A request Refindery would reject, with zero incremental impact."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["rejected"]
    index: int = Field(ge=0)
    detail: str


class EstimateBatchUnavailableResult(BaseModel):
    """A page whose live estimate failed and needs a fallback."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["unavailable"]
    index: int = Field(ge=0)
    detail: str


EstimateBatchResult = Annotated[
    EstimateBatchEstimatedResult
    | EstimateBatchRevisitResult
    | EstimateBatchBlacklistedResult
    | EstimateBatchRejectedResult
    | EstimateBatchUnavailableResult,
    Field(discriminator="outcome"),
]


class EstimateBatchResponse(BaseModel):
    """Configuration profile and per-item dry-run estimates."""

    model_config = ConfigDict(extra="forbid")

    profile: EstimateFallbackProfile
    results: list[EstimateBatchResult]


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


class PageStatusBatchRequest(BaseModel):
    """Body for ``POST /v1/pages/status/batch`` (Refindery >= 0.2.0)."""

    model_config = ConfigDict(extra="forbid")

    page_ids: list[str] = Field(min_length=1, max_length=MAX_STATUS_BATCH)


class PageStatusBatchFoundResult(BaseModel):
    """Status for a page the server still knows about."""

    found: Literal[True]
    page_id: str
    status: PageStatus
    last_error: str | None = None


class PageStatusBatchMissingResult(BaseModel):
    """Result for an unknown or expired page id."""

    found: Literal[False]
    page_id: str


PageStatusBatchResult = Annotated[
    PageStatusBatchFoundResult | PageStatusBatchMissingResult,
    Field(discriminator="found"),
]


class PageStatusBatchResponse(BaseModel):
    """Response for ``POST /v1/pages/status/batch``."""

    results: list[PageStatusBatchResult]


class ReadyzResponse(BaseModel):
    """Response for ``GET /readyz`` (capabilities absent before 0.2.0)."""

    status: str
    capabilities: dict[str, bool] = Field(default_factory=dict)


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
