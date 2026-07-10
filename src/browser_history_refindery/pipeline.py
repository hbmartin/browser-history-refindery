"""The import pipeline: read, merge, filter, submit, and track pages.

Reading and submitting overlap: a *producer* streams profiles one at a time
(read → merge → filter → enqueue) while the *submitter* drains the queue, so
submission of the first profile's URLs begins while later profiles are still
being read. Every enqueued ``UrlSubmission`` is the same object held in the
merge map, so a later profile's sighting mutates it in place *until it is sent*;
cross-profile merge is therefore best-effort in streaming mode (a URL already
submitted keeps the metadata it had at send time — the submissions table dedups
it, so the later sighting is not re-sent). A ``--limit`` run instead reads and
merges every profile before emitting, because the newest URLs across all
profiles must win the capped slots — there the merge is always complete.
"""

import asyncio
import contextlib
import hashlib
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import httpx2
from rich.console import Console
from rich.live import Live

from browser_history_refindery.api_client import (
    Accepted,
    Blacklisted,
    RefinderyClient,
    Revisit,
    ServerError,
    ValidationRejectedError,
)
from browser_history_refindery.api_models import IngestPageRequest
from browser_history_refindery.browsers import BrowserProfile, VisitRecord, read_profile
from browser_history_refindery.config import AppConfig
from browser_history_refindery.filters import ExclusionEngine, SkipReason
from browser_history_refindery.logsetup import logger
from browser_history_refindery.pacer import AdaptivePacer
from browser_history_refindery.state import StateStore
from browser_history_refindery.stats import ProfileStats, RunStats
from browser_history_refindery.ui import (
    build_progress,
    print_dry_run_report,
    print_summary,
    render_dashboard,
)

Emit = Callable[["UrlSubmission"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """One (browser, profile) sighting of a URL."""

    browser: str
    profile: str
    profile_key: str
    watermark_key: tuple[str, str]
    visit_count: int
    first_visit_at: datetime
    last_visit_at: datetime


@dataclass(slots=True)
class UrlSubmission:
    """One URL merged across every profile it appeared in."""

    url: str
    title: str | None
    sources: list[SourceInfo] = field(default_factory=list)
    attempts: int = 0
    last_submitted_visit_at: datetime | None = None

    def snapshot(self) -> Self:
        """Freeze the current merged shape for one HTTP attempt and its result."""
        return replace(self, sources=list(self.sources))

    @property
    def primary(self) -> SourceInfo:
        """The sighting with the most recent visit."""
        return max(self.sources, key=lambda source: source.last_visit_at)

    @property
    def first_visit_at(self) -> datetime:
        """Earliest visit across all sources."""
        return min(source.first_visit_at for source in self.sources)

    @property
    def last_visit_at(self) -> datetime:
        """Latest visit across all sources."""
        return max(source.last_visit_at for source in self.sources)

    @property
    def total_visits(self) -> int:
        """Combined visit count across all sources."""
        return sum(source.visit_count for source in self.sources)

    def to_request(self, hostname: str) -> IngestPageRequest:
        """Build the ingest request body for this URL."""
        primary = self.primary
        metadata: dict[str, Any] = {
            "browser": primary.browser,
            "profile": primary.profile,
            "visit_count": self.total_visits,
            "first_visit_at": self.first_visit_at.isoformat(),
            "last_visit_at": self.last_visit_at.isoformat(),
            "hostname": hostname,
        }
        if len(self.sources) > 1:
            metadata["sources"] = [
                {
                    "browser": source.browser,
                    "profile": source.profile,
                    "visit_count": source.visit_count,
                    "first_visit_at": source.first_visit_at.isoformat(),
                    "last_visit_at": source.last_visit_at.isoformat(),
                }
                for source in self.sources
            ]
        return IngestPageRequest(
            url=self.url,
            title=self.title,
            source=f"history-import:{primary.browser}",
            fetched_at=self.last_visit_at,
            metadata=metadata,
        )


class Disposition(StrEnum):
    """The non-skip outcome of classifying a merged URL before submission."""

    ENQUEUE = "enqueue"
    ALREADY = "already"
    REJECTED = "rejected"


def _merge_record(merged: dict[str, UrlSubmission], record: VisitRecord) -> None:
    source = SourceInfo(
        browser=record.profile.browser_id,
        profile=record.profile.profile_name,
        profile_key=record.profile.key,
        watermark_key=record.profile.watermark_key,
        visit_count=record.visit_count,
        first_visit_at=record.first_visit_at,
        last_visit_at=record.last_visit_at,
    )
    if (existing := merged.get(record.url)) is None:
        merged[record.url] = UrlSubmission(
            url=record.url, title=record.title, sources=[source]
        )
        return
    newest = record.last_visit_at > existing.last_visit_at
    existing.sources.append(source)
    if record.title and (newest or not existing.title):
        existing.title = record.title


def _short(url: str, limit: int = 60) -> str:
    return url if len(url) <= limit else f"{url[: limit - 1]}…"


def _log_url(url: str) -> str:
    """Render a URL for logs without exposing query or fragment contents."""
    parts = urlsplit(url)
    sensitive = urlunsplit(("", "", "", parts.query, parts.fragment))
    if not sensitive:
        return url
    fingerprint = hashlib.sha256(sensitive.encode()).hexdigest()[:12]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) + (
        f"?redacted={fingerprint}"
    )


def _classify(
    url: str,
    submission: UrlSubmission,
    *,
    submitted: dict[str, datetime],
    permanently_rejected: set[str],
    engine: ExclusionEngine,
    resubmit_revisits: bool,
) -> Disposition | SkipReason:
    """Decide what to do with one merged URL (enqueue, skip, or drop)."""
    if url in permanently_rejected:
        return Disposition.REJECTED
    if (prior := submitted.get(url)) is not None and (
        not resubmit_revisits or submission.last_visit_at <= prior
    ):
        return Disposition.ALREADY
    if reason := engine.check(url):
        return reason
    return Disposition.ENQUEUE


@dataclass(slots=True)
class _Planner:
    """Running merge/classify state shared across streamed profiles."""

    engine: ExclusionEngine
    stats: RunStats
    submitted: dict[str, datetime]
    permanently_rejected: set[str]
    resubmit_revisits: bool
    merged: dict[str, UrlSubmission] = field(default_factory=dict)
    classified: set[str] = field(default_factory=set)
    already: set[str] = field(default_factory=set)

    def merge_profile(
        self, records: list[VisitRecord]
    ) -> tuple[datetime | None, list[str]]:
        """Merge records and reconsider deduped URLs when a newer visit appears."""
        profile_max: datetime | None = None
        touched: list[str] = []
        touched_urls: set[str] = set()
        for record in records:
            if profile_max is None or record.last_visit_at > profile_max:
                profile_max = record.last_visit_at
            _merge_record(self.merged, record)
            if record.url in touched_urls:
                continue
            if record.url not in self.classified:
                touched.append(record.url)
                touched_urls.add(record.url)
                continue
            submission = self.merged[record.url]
            if (
                self.resubmit_revisits
                and submission.last_submitted_visit_at is not None
                and submission.last_visit_at > submission.last_submitted_visit_at
            ):
                self.classified.remove(record.url)
                touched.append(record.url)
                touched_urls.add(record.url)
                continue
            prior = self.submitted.get(record.url)
            if (
                self.resubmit_revisits
                and record.url in self.already
                and prior is not None
                and self.merged[record.url].last_visit_at > prior
            ):
                self.classified.remove(record.url)
                self.already.remove(record.url)
                self.stats.already_submitted -= 1
                touched.append(record.url)
                touched_urls.add(record.url)
        return profile_max, touched

    def classify_profile(
        self, touched: list[str]
    ) -> tuple[list[tuple[str, SkipReason]], list[str]]:
        """Classify a profile's newly-seen URLs.

        Counts skip/already/rejected outcomes and returns the skips to persist
        plus the URLs eligible for submission (newest-first). Emission is left to
        the caller so bounded (``--limit``) and unbounded runs can differ.
        """
        skips: list[tuple[str, SkipReason]] = []
        enqueue: list[str] = []
        ordered = sorted(
            touched, key=lambda u: self.merged[u].last_visit_at, reverse=True
        )
        for url in ordered:
            self.classified.add(url)
            result = _classify(
                url,
                self.merged[url],
                submitted=self.submitted,
                permanently_rejected=self.permanently_rejected,
                engine=self.engine,
                resubmit_revisits=self.resubmit_revisits,
            )
            if isinstance(result, SkipReason):
                skips.append((url, result))
                self.stats.skipped += 1
                self.stats.skip_reasons[str(result.kind)] += 1
            elif result is Disposition.ALREADY:
                self.stats.already_submitted += 1
                self.already.add(url)
            elif result is Disposition.REJECTED:
                self.stats.previously_rejected += 1
            else:
                enqueue.append(url)
        return skips, enqueue

    async def emit_one(self, url: str, emit: Emit) -> None:
        """Emit one submission and update the queued counters."""
        submission = self.merged[url]
        await emit(submission)
        self.stats.total_to_submit += 1
        if pstats := self.stats.per_profile.get(submission.primary.profile_key):
            pstats.queued_for_submit += 1


async def _read_profile_into(
    planner: _Planner,
    profile: BrowserProfile,
    *,
    state: StateStore,
    stats: RunStats,
    run_id: int,
    ignore_watermarks: bool,
) -> tuple[datetime | None, list[str]]:
    """Read one profile, merge it, and record its skips. Returns (max, enqueue)."""
    since = None if ignore_watermarks else await state.get_watermark(profile)
    pstats = stats.per_profile.setdefault(
        profile.key, ProfileStats(label=profile.display)
    )
    records = await asyncio.to_thread(read_profile, profile, since=since)
    pstats.urls_read += len(records)
    stats.urls_read_total += len(records)
    logger.debug("read {} urls from {}", len(records), profile.display)
    profile_max, touched = planner.merge_profile(records)
    stats.unique_urls = len(planner.merged)
    skips, enqueue = planner.classify_profile(touched)
    if skips:
        await state.record_skips(skips, run_id)
    pstats.done = True
    stats.profiles_read += 1
    return profile_max, enqueue


async def _stream_profiles(
    profiles: list[BrowserProfile],
    *,
    engine: ExclusionEngine,
    state: StateStore,
    stats: RunStats,
    config: AppConfig,
    run_id: int,
    ignore_watermarks: bool,
    limit: int | None,
    shutdown: asyncio.Event,
    emit: Emit,
) -> dict[BrowserProfile, datetime]:
    """Read, merge, filter, and emit history for submission.

    With no ``--limit`` this truly streams: each profile's eligible URLs are
    emitted as soon as it is read, so submission overlaps reading the rest.
    Cross-profile merge is best-effort in that mode — a URL already sent before a
    later profile is read keeps the metadata it had at send time. With a
    ``--limit`` the newest-across-all-profiles URLs must win the capped slots, so
    all profiles are read and merged first, then the top URLs are emitted;
    profiles whose URLs are dropped keep their watermark unset for the next run.
    """
    planner = _Planner(
        engine=engine,
        stats=stats,
        submitted=await state.load_submission_visit_times(),
        permanently_rejected=await state.load_rejected_urls(),
        resubmit_revisits=config.import_.resubmit_revisits,
    )
    stats.profiles_total = len(profiles)
    read_kwargs = {
        "state": state,
        "stats": stats,
        "run_id": run_id,
        "ignore_watermarks": ignore_watermarks,
    }
    if limit is None:
        watermarks = await _emit_streaming(
            profiles, planner=planner, shutdown=shutdown, emit=emit, **read_kwargs
        )
    else:
        watermarks = await _emit_limited(
            profiles,
            planner=planner,
            shutdown=shutdown,
            emit=emit,
            limit=limit,
            **read_kwargs,
        )
    stats.reading_finished = True
    return watermarks


async def _emit_streaming(
    profiles: list[BrowserProfile],
    *,
    planner: _Planner,
    state: StateStore,
    stats: RunStats,
    run_id: int,
    ignore_watermarks: bool,
    shutdown: asyncio.Event,
    emit: Emit,
) -> dict[BrowserProfile, datetime]:
    watermarks: dict[BrowserProfile, datetime] = {}
    for profile in profiles:
        if shutdown.is_set():
            break
        profile_max, enqueue = await _read_profile_into(
            planner,
            profile,
            state=state,
            stats=stats,
            run_id=run_id,
            ignore_watermarks=ignore_watermarks,
        )
        for url in enqueue:
            await planner.emit_one(url, emit)
        if profile_max is not None:
            watermarks[profile] = profile_max
    return watermarks


async def _emit_limited(
    profiles: list[BrowserProfile],
    *,
    planner: _Planner,
    state: StateStore,
    stats: RunStats,
    run_id: int,
    ignore_watermarks: bool,
    shutdown: asyncio.Event,
    emit: Emit,
    limit: int,
) -> dict[BrowserProfile, datetime]:
    per_profile_max: dict[BrowserProfile, datetime] = {}
    candidates: list[str] = []
    for profile in profiles:
        if shutdown.is_set():
            break
        profile_max, enqueue = await _read_profile_into(
            planner,
            profile,
            state=state,
            stats=stats,
            run_id=run_id,
            ignore_watermarks=ignore_watermarks,
        )
        candidates.extend(enqueue)
        if profile_max is not None:
            per_profile_max[profile] = profile_max
    candidates.sort(key=lambda u: planner.merged[u].last_visit_at, reverse=True)
    kept, dropped = candidates[:limit], candidates[limit:]
    dropped_keys = {
        source.watermark_key
        for url in dropped
        for source in planner.merged[url].sources
    }
    if dropped:
        logger.info(
            "limit {} reached; {} URLs deferred to a later run", limit, len(dropped)
        )
    for url in kept:
        await planner.emit_one(url, emit)
    return {
        profile: profile_max
        for profile, profile_max in per_profile_max.items()
        if profile.watermark_key not in dropped_keys
    }


class _Runner:
    """Shared context for the concurrent producer/submitter/poller/backlog tasks."""

    def __init__(
        self,
        *,
        client: RefinderyClient,
        state: StateStore,
        stats: RunStats,
        config: AppConfig,
        run_id: int,
        profiles: list[BrowserProfile],
        engine: ExclusionEngine,
        ignore_watermarks: bool,
        limit: int | None,
    ) -> None:
        self.client = client
        self.state = state
        self.stats = stats
        self.config = config
        self.run_id = run_id
        self.profiles = profiles
        self.engine = engine
        self.ignore_watermarks = ignore_watermarks
        self.limit = limit
        self.hostname = socket.gethostname()
        self.shutdown = asyncio.Event()
        self.producer_done = asyncio.Event()
        self.queue: asyncio.Queue[UrlSubmission] = asyncio.Queue()
        self.watermarks: dict[BrowserProfile, datetime] = {}
        self.pacer = AdaptivePacer(config=config.pacing, sleep=self.interruptible_sleep)

    async def interruptible_sleep(self, seconds: float) -> None:
        """Sleep that wakes early when shutdown is requested."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.shutdown.wait(), timeout=seconds)

    async def producer(self) -> None:
        """Stream profiles into the queue, then mark reading complete."""
        try:
            self.watermarks = await _stream_profiles(
                self.profiles,
                engine=self.engine,
                state=self.state,
                stats=self.stats,
                config=self.config,
                run_id=self.run_id,
                ignore_watermarks=self.ignore_watermarks,
                limit=self.limit,
                shutdown=self.shutdown,
                emit=self._emit,
            )
        finally:
            self.producer_done.set()

    async def _emit(self, submission: UrlSubmission) -> None:
        self.queue.put_nowait(submission)

    async def submitter(self) -> None:
        """Drain the queue through the pacer, recording every outcome."""
        while not self.shutdown.is_set():
            item = await self._next_item()
            if item is None:
                break
            self.stats.current_interval = self.pacer.effective_interval
            await self.pacer.wait()
            if self.shutdown.is_set():
                self.queue.put_nowait(item)
                break
            await self._submit_one(item)
        self.stats.submitter_finished = True

    async def _next_item(self) -> UrlSubmission | None:
        """Pull the next queued URL, waiting for the producer while it reads."""
        while not self.shutdown.is_set():
            try:
                return self.queue.get_nowait()
            except asyncio.QueueEmpty:
                if self.producer_done.is_set():
                    return None
                await self.interruptible_sleep(0.1)
        return None

    async def _submit_one(self, item: UrlSubmission) -> None:
        attempted = item.snapshot()
        try:
            outcome = await self.client.ingest_url(attempted.to_request(self.hostname))
        except (httpx2.TransportError, ServerError) as exc:
            self._handle_submit_error(item, exc)
            return
        except ValidationRejectedError as exc:
            self.pacer.on_success()
            self.stats.submitted += 1
            self.stats.rejected += 1
            if profile_stats := self.stats.per_profile.get(
                attempted.primary.profile_key
            ):
                profile_stats.submitted += 1
            display_url = _log_url(item.url)
            self.stats.add_event(f"rejected {_short(display_url)}: {exc}")
            logger.info("rejected (422) {}: {}", display_url, exc)
            await self.state.record_submission(
                url=attempted.url,
                outcome="rejected",
                run_id=self.run_id,
                last_visit_at=attempted.last_visit_at,
                last_error=str(exc),
            )
            return
        self.pacer.on_success()
        self.stats.submitted += 1
        if profile_stats := self.stats.per_profile.get(attempted.primary.profile_key):
            profile_stats.submitted += 1
        await self._record_outcome(attempted, outcome)
        if isinstance(outcome, Accepted | Revisit):
            item.last_submitted_visit_at = attempted.last_visit_at
            if (
                self.config.import_.resubmit_revisits
                and item.last_visit_at > attempted.last_visit_at
            ):
                self.queue.put_nowait(item)
                self.stats.total_to_submit += 1
                if profile_stats := self.stats.per_profile.get(
                    item.primary.profile_key
                ):
                    profile_stats.queued_for_submit += 1

    async def _record_outcome(
        self, item: UrlSubmission, outcome: Accepted | Revisit | Blacklisted
    ) -> None:
        match outcome:
            case Accepted(page_id=page_id):
                self.stats.accepted += 1
                logger.debug("accepted (202) {} -> {}", _log_url(item.url), page_id)
                await self.state.record_submission(
                    url=item.url,
                    outcome="queued",
                    run_id=self.run_id,
                    last_visit_at=item.last_visit_at,
                    page_id=page_id,
                    server_status="queued",
                )
            case Revisit(page_id=page_id, status=status) as revisit:
                self.stats.revisits += 1
                if revisit.content_hash_differs:
                    self.stats.add_event(
                        f"content changed: {_short(_log_url(item.url))}"
                    )
                logger.debug(
                    "revisit (200) {} -> {} ({})",
                    _log_url(item.url),
                    page_id,
                    status,
                )
                await self.state.record_submission(
                    url=item.url,
                    outcome="revisit",
                    run_id=self.run_id,
                    last_visit_at=item.last_visit_at,
                    page_id=page_id,
                    server_status=status,
                )
            case Blacklisted(pattern=pattern):
                self.stats.blacklisted += 1
                self.stats.add_event(
                    f"blacklisted by server ({pattern}): {_short(_log_url(item.url))}"
                )
                logger.info("blacklisted (403) {} by {}", _log_url(item.url), pattern)
                await self.state.record_submission(
                    url=item.url,
                    outcome="blacklisted",
                    run_id=self.run_id,
                    last_visit_at=item.last_visit_at,
                )

    def _handle_submit_error(self, item: UrlSubmission, exc: Exception) -> None:
        self.pacer.on_failure()
        item.attempts += 1
        if item.attempts >= self.config.pacing.max_attempts:
            self.stats.errors += 1
            display_url = _log_url(item.url)
            self.stats.add_event(f"gave up on {_short(display_url)}: {exc}")
            logger.error(
                "gave up on {} after {} attempts: {}", display_url, item.attempts, exc
            )
        else:
            self.stats.retries += 1
            self.queue.put_nowait(item)
            display_url = _log_url(item.url)
            self.stats.add_event(
                f"retry {item.attempts}/{self.config.pacing.max_attempts}: "
                f"{_short(display_url)}"
            )
            logger.warning(
                "retry {}/{} {}: {}",
                item.attempts,
                self.config.pacing.max_attempts,
                display_url,
                exc,
            )

    async def poller(self) -> None:
        """Track submitted pages toward a terminal indexing status."""
        grace_deadline: float | None = None
        batch_size = self.config.poller.batch_size
        while not self.shutdown.is_set():
            page_ids = await self.state.nonterminal_pages(limit=batch_size)
            if not page_ids and self.stats.submitter_finished:
                break
            await self._poll_batch(page_ids)
            if self.stats.submitter_finished:
                if grace_deadline is None:
                    grace_deadline = time.monotonic() + self.config.poller.drain_grace
                elif time.monotonic() > grace_deadline:
                    self.stats.add_event(
                        "some pages still indexing; run status-sweep later"
                    )
                    break
            await self.interruptible_sleep(self.config.poller.interval)

    async def _poll_batch(self, page_ids: list[str]) -> None:
        for page_id in page_ids:
            if self.shutdown.is_set():
                return
            try:
                status = await self.client.page_status(page_id)
            except (httpx2.HTTPError, ServerError):
                continue
            await self.state.update_page_status(
                page_id=page_id, status=status.status, last_error=status.last_error
            )
            match status.status:
                case "indexed":
                    self.stats.indexed += 1
                case "dead":
                    self.stats.dead += 1
                    self.stats.add_event(
                        f"dead: {page_id} ({status.last_error or 'unknown error'})"
                    )
                    logger.info("dead: {} ({})", page_id, status.last_error)
                case _:
                    pass
            await asyncio.sleep(0.1)

    async def backlog_watcher(self) -> None:
        """Feed the server's pending-job depth into the pacer."""
        limit = self.config.pacing.queue_depth_threshold + 1
        while not self.shutdown.is_set() and not self.stats.submitter_finished:
            with contextlib.suppress(httpx2.HTTPError, ServerError):
                depth = await self.client.pending_job_count(limit=limit)
                self.pacer.on_backlog(depth)
                self.stats.server_backlog = depth
            await self.interruptible_sleep(self.config.pacing.queue_poll_interval)


def _runtime_error_from_group(group: ExceptionGroup) -> RuntimeError | None:
    """Return the first leaf when every grouped failure is a RuntimeError."""
    runtime_errors, remainder = group.split(RuntimeError)
    if runtime_errors is None or remainder is not None:
        return None
    error: BaseException = runtime_errors
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error if isinstance(error, RuntimeError) else None


async def _run_tasks(runner: _Runner, console: Console) -> bool:
    """Run producer, submitter, poller, backlog + live UI; True if interrupted."""
    loop = asyncio.get_running_loop()
    tasks: list[asyncio.Task[None]] = []
    presses = 0

    def on_sigint() -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            runner.stats.add_event(
                "interrupt: finishing in-flight request (Ctrl-C again to force quit)"
            )
            runner.shutdown.set()
        else:
            for task in tasks:
                task.cancel()

    loop.add_signal_handler(signal.SIGINT, on_sigint)
    progress, reading_id, submit_id = build_progress()
    try:
        with Live(console=console, refresh_per_second=8) as live:

            def paint() -> None:
                stats = runner.stats
                stats.queue_depth = runner.queue.qsize()
                progress.update(
                    reading_id,
                    total=max(stats.profiles_total, 1),
                    completed=stats.profiles_read,
                )
                progress.update(
                    submit_id, total=stats.total_to_submit, completed=stats.processed
                )
                live.update(render_dashboard(stats, progress))

            async def refresh() -> None:
                while True:
                    paint()
                    await asyncio.sleep(0.25)

            refresh_task = asyncio.create_task(refresh())
            try:
                try:
                    async with asyncio.TaskGroup() as tg:
                        tasks.append(tg.create_task(runner.producer()))
                        tasks.append(tg.create_task(runner.submitter()))
                        tasks.append(tg.create_task(runner.poller()))
                        tasks.append(tg.create_task(runner.backlog_watcher()))
                except ExceptionGroup as exc:
                    if error := _runtime_error_from_group(exc):
                        raise error from exc
                    raise
            finally:
                refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresh_task
                paint()
    finally:
        loop.remove_signal_handler(signal.SIGINT)
    return (
        runner.shutdown.is_set()
        or runner.stats.processed < runner.stats.total_to_submit
    )


async def _dry_run(
    *,
    profiles: list[BrowserProfile],
    engine: ExclusionEngine,
    state: StateStore,
    stats: RunStats,
    config: AppConfig,
    run_id: int,
    ignore_watermarks: bool,
    limit: int | None,
    console: Console,
) -> None:
    collected: list[UrlSubmission] = []

    async def collect(submission: UrlSubmission) -> None:
        collected.append(submission)

    with console.status("reading browser history..."):
        await _stream_profiles(
            profiles,
            engine=engine,
            state=state,
            stats=stats,
            config=config,
            run_id=run_id,
            ignore_watermarks=ignore_watermarks,
            limit=limit,
            shutdown=asyncio.Event(),
            emit=collect,
        )
    print_dry_run_report(console, stats, collected)


async def run_import(
    *,
    config: AppConfig,
    profiles: list[BrowserProfile],
    console: Console,
    dry_run: bool = False,
    limit: int | None = None,
    ignore_watermarks: bool = False,
) -> RunStats:
    """Execute one import run end to end and return its statistics."""
    engine = ExclusionEngine(config.exclusions)
    stats = RunStats()
    async with StateStore(config.state.db_path) as state:
        run_id = await state.begin_run()
        interrupted = True
        try:
            if dry_run:
                await _dry_run(
                    profiles=profiles,
                    engine=engine,
                    state=state,
                    stats=stats,
                    config=config,
                    run_id=run_id,
                    ignore_watermarks=ignore_watermarks,
                    limit=limit,
                    console=console,
                )
                interrupted = False
                return stats
            token = config.server.resolve_token()
            async with RefinderyClient(
                base_url=config.server.base_url,
                auth_token=token,
                request_timeout=config.server.request_timeout,
                ready_timeout=config.server.ready_timeout,
            ) as client:
                with console.status("waiting for the server to be ready..."):
                    await client.wait_ready()
                runner = _Runner(
                    client=client,
                    state=state,
                    stats=stats,
                    config=config,
                    run_id=run_id,
                    profiles=profiles,
                    engine=engine,
                    ignore_watermarks=ignore_watermarks,
                    limit=limit,
                )
                logger.info("run {} started: {} profile(s)", run_id, len(profiles))
                interrupted = await _run_tasks(runner, console)
            if not interrupted and stats.errors == 0:
                for profile, watermark in runner.watermarks.items():
                    await state.set_watermark(profile, watermark)
            print_summary(console, stats, interrupted=interrupted)
            logger.info(
                "run {} done: submitted={} accepted={} revisits={} errors={} "
                "interrupted={}",
                run_id,
                stats.submitted,
                stats.accepted,
                stats.revisits,
                stats.errors,
                interrupted,
            )
        finally:
            await state.finish_run(
                run_id,
                interrupted=interrupted,
                urls_seen=stats.unique_urls,
                submitted=stats.accepted,
                revisits=stats.revisits,
                blacklisted=stats.blacklisted,
                rejected=stats.rejected,
                skipped=stats.skipped,
                errors=stats.errors,
            )
    return stats
