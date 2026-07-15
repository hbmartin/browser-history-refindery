"""Configuration-aware dry-run estimation and cached fallback aggregation."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit

import httpx2

from browser_history_refindery.api_client import (
    AuthError,
    InvalidEstimateResponseError,
    RefinderyClient,
    ServerError,
    ValidationRejectedError,
)
from browser_history_refindery.api_models import (
    EstimateBatchBlacklistedResult,
    EstimateBatchEstimatedResult,
    EstimateBatchRejectedResult,
    EstimateBatchResult,
    EstimateBatchRevisitResult,
    EstimateBatchUnavailableResult,
    EstimateFallbackProfile,
    IngestPageRequest,
)
from browser_history_refindery.config import AppConfig, MissingTokenError
from browser_history_refindery.state import StateStore


def refindery_domain(url: str) -> str:
    """Return the hostname grouping used by Refindery's canonicalizer."""
    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        hostname = None
    return "(unknown)" if hostname is None else hostname.lower().removeprefix("www.")


@dataclass(frozen=True, slots=True)
class DryRunEstimate:
    """Aggregated resource estimates and their confidence coverage."""

    total_pages: int
    domains: tuple[tuple[str, int], ...]
    storage_bytes: int | None
    cost_usd: Decimal | None
    cost_breakdown_usd: dict[str, Decimal]
    live_pages: int
    fallback_pages: int
    zero_impact_pages: int
    unavailable_pages: int
    unpriced_components: tuple[str, ...]
    fallback_profile_generated_at: datetime | None
    notes: tuple[str, ...]


@dataclass(slots=True)
class _EstimateAccumulator:
    total_pages: int
    domains: Counter[str]
    storage_bytes: int = 0
    cost_usd: Decimal = Decimal(0)
    cost_breakdown_usd: dict[str, Decimal] = field(default_factory=dict)
    live_pages: int = 0
    fallback_pages: int = 0
    zero_impact_pages: int = 0
    unavailable_pages: int = 0
    storage_complete: bool = True
    cost_complete: bool = True
    unpriced_components: set[str] = field(default_factory=set)
    fallback_profile_generated_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    def add_note(self, message: str) -> None:
        """Add one user-facing diagnostic without repeating identical failures."""
        if message not in self.notes:
            self.notes.append(message)

    def _add_cost(
        self,
        *,
        total: Decimal | None,
        breakdown: dict[str, Decimal],
        unpriced_components: Sequence[str],
    ) -> None:
        for component, amount in breakdown.items():
            self.cost_breakdown_usd[component] = (
                self.cost_breakdown_usd.get(component, Decimal(0)) + amount
            )
        if total is None:
            self.cost_complete = False
            self.unpriced_components.update(unpriced_components)
        else:
            self.cost_usd += total

    def apply_result(
        self,
        result: EstimateBatchResult,
        *,
        profile: EstimateFallbackProfile,
    ) -> None:
        """Fold one validated result into the aggregate."""
        match result:
            case EstimateBatchEstimatedResult(
                estimated_storage_bytes=storage,
                estimated_cost_usd=cost,
                cost_breakdown_usd=breakdown,
                unpriced_components=unpriced,
            ):
                self.live_pages += 1
                self.storage_bytes += storage
                self._add_cost(
                    total=cost,
                    breakdown=breakdown,
                    unpriced_components=unpriced,
                )
            case EstimateBatchUnavailableResult():
                self.apply_fallback(profile)
            case (
                EstimateBatchRevisitResult()
                | EstimateBatchBlacklistedResult()
                | EstimateBatchRejectedResult()
            ):
                self.zero_impact_pages += 1

    def apply_fallback(self, profile: EstimateFallbackProfile | None) -> None:
        """Apply one cached per-page estimate, or mark the page unresolved."""
        if profile is None:
            self.unavailable_pages += 1
            self.storage_complete = False
            self.cost_complete = False
            return
        self.fallback_pages += 1
        self.fallback_profile_generated_at = profile.generated_at
        self.storage_bytes += profile.storage_bytes_per_page
        self._add_cost(
            total=profile.cost_usd_per_page,
            breakdown=profile.cost_breakdown_usd_per_page,
            unpriced_components=profile.unpriced_components,
        )

    def finish(self) -> DryRunEstimate:
        """Freeze the aggregate for reporting."""
        if self.fallback_pages:
            generated = self.fallback_profile_generated_at
            suffix = "" if generated is None else f" generated {generated.isoformat()}"
            self.add_note(
                f"used a cached fallback profile{suffix} for "
                f"{self.fallback_pages} page(s)"
            )
        if self.unavailable_pages:
            self.add_note(
                f"{self.unavailable_pages} page(s) could not be estimated and no "
                "cached profile was available"
            )
        if self.unpriced_components:
            names = ", ".join(sorted(self.unpriced_components))
            self.add_note(f"total cost unavailable; unpriced components: {names}")
        return DryRunEstimate(
            total_pages=self.total_pages,
            domains=tuple(
                sorted(self.domains.items(), key=lambda item: (-item[1], item[0]))
            ),
            storage_bytes=self.storage_bytes if self.storage_complete else None,
            cost_usd=self.cost_usd if self.cost_complete else None,
            cost_breakdown_usd=dict(sorted(self.cost_breakdown_usd.items())),
            live_pages=self.live_pages,
            fallback_pages=self.fallback_pages,
            zero_impact_pages=self.zero_impact_pages,
            unavailable_pages=self.unavailable_pages,
            unpriced_components=tuple(sorted(self.unpriced_components)),
            fallback_profile_generated_at=self.fallback_profile_generated_at,
            notes=tuple(self.notes),
        )


def _new_accumulator(pages: Sequence[IngestPageRequest]) -> _EstimateAccumulator:
    return _EstimateAccumulator(
        total_pages=len(pages),
        domains=Counter(refindery_domain(page.url) for page in pages),
    )


def _apply_fallback_batch(
    accumulator: _EstimateAccumulator,
    *,
    count: int,
    profile: EstimateFallbackProfile | None,
) -> None:
    for _ in range(count):
        accumulator.apply_fallback(profile)


async def estimate_pages(
    pages: Sequence[IngestPageRequest],
    *,
    config: AppConfig,
    state: StateStore,
    batch_size: int,
) -> DryRunEstimate:
    """Attempt live estimates, falling back to the cached server profile."""
    accumulator = _new_accumulator(pages)
    if not pages:
        return accumulator.finish()

    profile = await state.get_estimation_profile(server_base_url=config.server.base_url)
    try:
        token = config.server.resolve_token()
    except MissingTokenError:
        token = None

    async with RefinderyClient(
        base_url=config.server.base_url,
        auth_token=token,
        request_timeout=config.server.request_timeout,
        ready_timeout=config.server.ready_timeout,
    ) as client:
        if not await client.probe_ready():
            accumulator.add_note("live estimate unavailable: Refindery is not ready")
            _apply_fallback_batch(accumulator, count=len(pages), profile=profile)
            return accumulator.finish()
        if not client.supports_batch_estimate:
            accumulator.add_note(
                "live estimate unavailable: Refindery does not advertise batch_estimate"
            )
            _apply_fallback_batch(accumulator, count=len(pages), profile=profile)
            return accumulator.finish()
        if token is None:
            accumulator.add_note(
                "live estimate unavailable: no Refindery auth token is configured"
            )
            _apply_fallback_batch(accumulator, count=len(pages), profile=profile)
            return accumulator.finish()

        for start in range(0, len(pages), batch_size):
            batch = list(pages[start : start + batch_size])
            try:
                response = await client.estimate_batch(batch)
            except (
                AuthError,
                httpx2.HTTPError,
                InvalidEstimateResponseError,
                ServerError,
                ValidationRejectedError,
                ValueError,
            ) as exc:
                accumulator.add_note(
                    f"live estimate batch unavailable for {len(batch)} page(s): {exc}"
                )
                _apply_fallback_batch(accumulator, count=len(batch), profile=profile)
                continue

            profile = response.profile
            await state.set_estimation_profile(
                server_base_url=config.server.base_url, profile=profile
            )
            for result in response.results:
                accumulator.apply_result(result, profile=profile)

    return accumulator.finish()
