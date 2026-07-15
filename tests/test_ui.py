"""Smoke tests for the detailed dashboard and end-of-run reports."""

import io
from decimal import Decimal

from rich.console import Console

from browser_history_refindery.estimation import DryRunEstimate
from browser_history_refindery.stats import ProfileStats, RunStats
from browser_history_refindery.ui import (
    build_progress,
    print_dry_run_report,
    print_summary,
    render_dashboard,
)


def _populated_stats() -> RunStats:
    stats = RunStats(
        profiles_total=2,
        profiles_read=1,
        urls_read_total=120,
        unique_urls=100,
        total_to_submit=80,
        submitted=40,
        accepted=30,
        revisits=5,
        blacklisted=2,
        rejected=1,
        skipped=6,
        already_submitted=8,
        previously_rejected=2,
        retries=3,
        indexed=25,
        dead=1,
        current_interval=1.5,
        queue_depth=40,
        server_backlog=12,
    )
    stats.skip_reasons.update({"scheme": 4, "category": 2})
    stats.per_profile["chrome:Default"] = ProfileStats(
        label="Chrome — Default", urls_read=80, queued_for_submit=60, submitted=40
    )
    stats.per_profile["safari:Safari"] = ProfileStats(
        label="Safari", urls_read=40, queued_for_submit=20, submitted=0, done=False
    )
    stats.add_event("something happened")
    return stats


def _capture(renderable) -> str:
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, width=100).print(renderable)
    return buffer.getvalue()


def test_render_dashboard_includes_detail():
    stats = _populated_stats()
    progress, _reading, _submit = build_progress()
    text = _capture(render_dashboard(stats, progress))
    for token in ("reading history", "queued", "retries", "server backlog", "unique"):
        assert token in text


def test_print_summary_includes_new_counters():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    print_summary(console, _populated_stats(), interrupted=False)
    output = buffer.getvalue()
    assert "history URLs read" in output
    assert "retries" in output
    assert "throughput" in output


def test_print_summary_interrupted_warns():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    print_summary(console, _populated_stats(), interrupted=True)
    assert "interrupted" in buffer.getvalue().lower()


def test_dry_run_report_lists_urls():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)

    class _Sub:
        def __init__(self, url: str) -> None:
            self.url = url

    submissions = [_Sub(f"https://example.com/{index}") for index in range(12)]
    estimate = DryRunEstimate(
        total_pages=12,
        domains=(("example.com", 10), ("docs.example.com", 2)),
        storage_bytes=1_572_864,
        cost_usd=Decimal("0.0125"),
        cost_breakdown_usd={"embedding": Decimal("0.0125")},
        live_pages=9,
        fallback_pages=1,
        zero_impact_pages=2,
        unavailable_pages=0,
        unpriced_components=(),
        fallback_profile_generated_at=None,
        notes=("used a cached fallback profile for 1 page(s)",),
    )
    print_dry_run_report(console, _populated_stats(), submissions, estimate=estimate)
    output = buffer.getvalue()
    assert "Dry run" in output
    assert "and 2 more" in output  # 12 submissions, first 10 shown
    assert "eligible pages" in output
    assert "example.com" in output
    assert "docs.example.com" in output
    assert "1.5 MiB" in output
    assert "$0.0125 USD" in output
    assert "embedding" in output
