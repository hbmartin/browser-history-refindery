"""The import pipeline: read, merge, filter, submit, and track pages."""

import asyncio
import contextlib
import signal
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
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
from browser_history_refindery.pacer import AdaptivePacer
from browser_history_refindery.state import StateStore
from browser_history_refindery.stats import ProfileStats, RunStats
from browser_history_refindery.ui import (
    build_progress,
    print_dry_run_report,
    print_summary,
    render_dashboard,
)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """One (browser, profile) sighting of a URL."""

    browser: str
    profile: str
    profile_key: str
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


@dataclass(slots=True)
class ImportPlan:
    """Everything the submitter needs, computed up front."""

    submissions: list[UrlSubmission]
    watermarks: dict[BrowserProfile, datetime]
    urls_seen: int


def _merge_record(merged: dict[str, UrlSubmission], record: VisitRecord) -> None:
    source = SourceInfo(
        browser=record.profile.browser_id,
        profile=record.profile.profile_name,
        profile_key=record.profile.key,
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
    if newest and record.title:
        existing.title = record.title


def _short(url: str, limit: int = 60) -> str:
    return url if len(url) <= limit else f"{url[: limit - 1]}…"


async def _build_plan(
    profiles: list[BrowserProfile],
    *,
    engine: ExclusionEngine,
    state: StateStore,
    stats: RunStats,
    config: AppConfig,
    run_id: int,
    ignore_watermarks: bool,
    limit: int | None,
) -> ImportPlan:
    merged: dict[str, UrlSubmission] = {}
    watermarks: dict[BrowserProfile, datetime] = {}
    for profile in profiles:
        since = None if ignore_watermarks else await state.get_watermark(profile)
        records = await asyncio.to_thread(read_profile, profile, since=since)
        stats.per_profile[profile.key] = ProfileStats(
            label=profile.display, urls_read=len(records)
        )
        for record in records:
            _merge_record(merged, record)
        if records:
            watermarks[profile] = max(record.last_visit_at for record in records)
    submitted = await state.load_submission_times()
    keep, skips = _filter_merged(
        merged,
        submitted=submitted,
        engine=engine,
        stats=stats,
        resubmit_revisits=config.import_.resubmit_revisits,
    )
    await state.record_skips(skips, run_id)
    keep.sort(key=lambda submission: submission.last_visit_at, reverse=True)
    if limit is not None:
        keep = keep[:limit]
    stats.total_to_submit = len(keep)
    for submission in keep:
        if profile_stats := stats.per_profile.get(submission.primary.profile_key):
            profile_stats.queued_for_submit += 1
    return ImportPlan(submissions=keep, watermarks=watermarks, urls_seen=len(merged))


def _filter_merged(
    merged: dict[str, UrlSubmission],
    *,
    submitted: dict[str, datetime],
    engine: ExclusionEngine,
    stats: RunStats,
    resubmit_revisits: bool,
) -> tuple[list[UrlSubmission], list[tuple[str, SkipReason]]]:
    keep: list[UrlSubmission] = []
    skips: list[tuple[str, SkipReason]] = []
    for url, submission in merged.items():
        if (prior := submitted.get(url)) is not None and (
            not resubmit_revisits or submission.last_visit_at <= prior
        ):
            stats.already_submitted += 1
            continue
        if reason := engine.check(url):
            skips.append((url, reason))
            stats.skipped += 1
            stats.skip_reasons[str(reason.kind)] += 1
            continue
        keep.append(submission)
    return keep, skips


class _Runner:
    """Shared context for the concurrent submitter/poller/backlog tasks."""

    def __init__(
        self,
        *,
        client: RefinderyClient,
        state: StateStore,
        stats: RunStats,
        config: AppConfig,
        run_id: int,
    ) -> None:
        self.client = client
        self.state = state
        self.stats = stats
        self.config = config
        self.run_id = run_id
        self.hostname = socket.gethostname()
        self.shutdown = asyncio.Event()
        self.queue: asyncio.Queue[UrlSubmission] = asyncio.Queue()
        self.pacer = AdaptivePacer(config=config.pacing, sleep=self.interruptible_sleep)

    async def interruptible_sleep(self, seconds: float) -> None:
        """Sleep that wakes early when shutdown is requested."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.shutdown.wait(), timeout=seconds)

    async def submitter(self) -> None:
        """Drain the queue through the pacer, recording every outcome."""
        while not self.shutdown.is_set():
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.stats.current_interval = self.pacer.effective_interval
            await self.pacer.wait()
            if self.shutdown.is_set():
                self.queue.put_nowait(item)
                break
            await self._submit_one(item)
        self.stats.submitter_finished = True

    async def _submit_one(self, item: UrlSubmission) -> None:
        try:
            outcome = await self.client.ingest_url(item.to_request(self.hostname))
        except (httpx.TransportError, ServerError) as exc:
            self._handle_submit_error(item, exc)
            return
        except ValidationRejectedError as exc:
            self.stats.errors += 1
            self.stats.add_event(f"rejected {_short(item.url)}: {exc}")
            return
        self.pacer.on_success()
        self.stats.submitted += 1
        if profile_stats := self.stats.per_profile.get(item.primary.profile_key):
            profile_stats.submitted += 1
        await self._record_outcome(item, outcome)

    async def _record_outcome(
        self, item: UrlSubmission, outcome: Accepted | Revisit | Blacklisted
    ) -> None:
        match outcome:
            case Accepted(page_id=page_id):
                self.stats.accepted += 1
                await self.state.record_submission(
                    url=item.url,
                    outcome="queued",
                    run_id=self.run_id,
                    page_id=page_id,
                    server_status="queued",
                )
            case Revisit(page_id=page_id, status=status) as revisit:
                self.stats.revisits += 1
                if revisit.content_hash_differs:
                    self.stats.add_event(f"content changed: {_short(item.url)}")
                await self.state.record_submission(
                    url=item.url,
                    outcome="revisit",
                    run_id=self.run_id,
                    page_id=page_id,
                    server_status=status,
                )
            case Blacklisted(pattern=pattern):
                self.stats.blacklisted += 1
                self.stats.add_event(
                    f"blacklisted by server ({pattern}): {_short(item.url)}"
                )
                await self.state.record_submission(
                    url=item.url, outcome="blacklisted", run_id=self.run_id
                )

    def _handle_submit_error(self, item: UrlSubmission, exc: Exception) -> None:
        self.pacer.on_failure()
        item.attempts += 1
        if item.attempts >= self.config.pacing.max_attempts:
            self.stats.errors += 1
            self.stats.add_event(f"gave up on {_short(item.url)}: {exc}")
        else:
            self.queue.put_nowait(item)
            self.stats.add_event(
                f"retry {item.attempts}/{self.config.pacing.max_attempts}: "
                f"{_short(item.url)}"
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
            except (httpx.HTTPError, ServerError):
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
                case _:
                    pass
            await asyncio.sleep(0.1)

    async def backlog_watcher(self) -> None:
        """Feed the server's pending-job depth into the pacer."""
        limit = self.config.pacing.queue_depth_threshold + 1
        while not self.shutdown.is_set() and not self.stats.submitter_finished:
            with contextlib.suppress(httpx.HTTPError, ServerError):
                depth = await self.client.pending_job_count(limit=limit)
                self.pacer.on_backlog(depth)
                self.stats.server_backlog = depth
            await self.interruptible_sleep(self.config.pacing.queue_poll_interval)


async def _run_tasks(runner: _Runner, plan: ImportPlan, console: Console) -> bool:
    """Run submitter, poller, backlog watcher, and the live UI. True if interrupted."""
    for item in plan.submissions:
        runner.queue.put_nowait(item)
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
    progress, task_id = build_progress(total=runner.stats.total_to_submit)
    try:
        with Live(console=console, refresh_per_second=8) as live:

            async def refresh() -> None:
                while True:
                    progress.update(task_id, completed=runner.stats.processed)
                    live.update(render_dashboard(runner.stats, progress))
                    await asyncio.sleep(0.25)

            refresh_task = asyncio.create_task(refresh())
            try:
                async with asyncio.TaskGroup() as tg:
                    tasks.append(tg.create_task(runner.submitter()))
                    tasks.append(tg.create_task(runner.poller()))
                    tasks.append(tg.create_task(runner.backlog_watcher()))
            finally:
                refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresh_task
                progress.update(task_id, completed=runner.stats.processed)
                live.update(render_dashboard(runner.stats, progress))
    finally:
        loop.remove_signal_handler(signal.SIGINT)
    return (
        runner.shutdown.is_set()
        or runner.stats.processed < runner.stats.total_to_submit
    )


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
        urls_seen = 0
        try:
            with console.status("reading browser history..."):
                plan = await _build_plan(
                    profiles,
                    engine=engine,
                    state=state,
                    stats=stats,
                    config=config,
                    run_id=run_id,
                    ignore_watermarks=ignore_watermarks,
                    limit=limit,
                )
            urls_seen = plan.urls_seen
            if dry_run:
                print_dry_run_report(console, stats, plan.submissions)
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
                )
                interrupted = await _run_tasks(runner, plan, console)
            if not interrupted and stats.errors == 0:
                for profile, watermark in plan.watermarks.items():
                    await state.set_watermark(profile, watermark)
            print_summary(console, stats, interrupted=interrupted)
        finally:
            await state.finish_run(
                run_id,
                interrupted=interrupted,
                urls_seen=urls_seen,
                submitted=stats.accepted,
                revisits=stats.revisits,
                blacklisted=stats.blacklisted,
                skipped=stats.skipped,
                errors=stats.errors,
            )
    return stats
