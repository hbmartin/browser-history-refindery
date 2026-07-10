"""Profile selection, the live import dashboard, and end-of-run reports."""

from typing import TYPE_CHECKING

import questionary
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from browser_history_refindery.browsers import BrowserProfile
from browser_history_refindery.stats import RunStats

if TYPE_CHECKING:
    from browser_history_refindery.pipeline import UrlSubmission


def select_profiles(profiles: list[BrowserProfile]) -> list[BrowserProfile]:
    """Interactively multi-select browser profiles (all checked by default)."""
    choices = [
        questionary.Choice(
            title=f"{profile.display} ({profile.profile_dir})",
            value=profile,
            checked=True,
        )
        for profile in profiles
    ]
    selected = questionary.checkbox(
        "Select browser profiles to import history from:", choices=choices
    ).ask()
    return selected if selected is not None else []


def build_progress() -> tuple[Progress, TaskID, TaskID]:
    """Create the reading and submitting progress bars for the dashboard."""
    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )
    reading_id = progress.add_task("reading   ", total=1)
    submit_id = progress.add_task("submitting", total=0)
    return progress, reading_id, submit_id


def _phase(stats: RunStats) -> str:
    if not stats.reading_finished:
        return "[cyan]reading history[/]"
    if not stats.submitter_finished:
        return "[cyan]submitting[/]"
    return "[cyan]draining (indexing)[/]"


def _header(stats: RunStats) -> Table:
    table = Table.grid(padding=(0, 2))
    for _ in range(6):
        table.add_column()
    table.add_row(
        "phase",
        _phase(stats),
        "elapsed",
        f"{stats.elapsed_seconds:.0f}s",
        "rate",
        f"{stats.throughput:.1f}/s",
    )
    profiles = f"{stats.profiles_read}/{stats.profiles_total}"
    table.add_row(
        "profiles",
        profiles,
        "read",
        str(stats.urls_read_total),
        "unique",
        str(stats.unique_urls),
    )
    return table


def _counters_table(stats: RunStats) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_column(style="bold")
    table.add_column(justify="right")
    backlog = "?" if stats.server_backlog is None else str(stats.server_backlog)
    table.add_row(
        "queued", str(stats.total_to_submit), "in queue", str(stats.queue_depth)
    )
    table.add_row(
        "new (202)",
        f"[green]{stats.accepted}[/]",
        "indexed",
        f"[green]{stats.indexed}[/]",
    )
    table.add_row(
        "revisits (200)", str(stats.revisits), "dead", f"[red]{stats.dead}[/]"
    )
    table.add_row(
        "blacklisted (403)",
        f"[yellow]{stats.blacklisted}[/]",
        "retries",
        f"[yellow]{stats.retries}[/]",
    )
    table.add_row(
        "rejected (422)",
        f"[yellow]{stats.rejected}[/]",
        "errors",
        f"[red]{stats.errors}[/]",
    )
    table.add_row(
        "skipped locally",
        f"[dim]{stats.skipped_locally}[/]",
        "server backlog",
        backlog,
    )
    table.add_row("interval", f"{stats.current_interval:.2f}s", "", "")
    return table


def _profiles_table(stats: RunStats, *, with_status: bool = False) -> Table:
    table = Table(box=None, pad_edge=False)
    table.add_column("profile", style="cyan", no_wrap=True)
    if with_status:
        table.add_column("status")
    table.add_column("read", justify="right")
    table.add_column("queued", justify="right")
    table.add_column("sent", justify="right")
    for profile_stats in stats.per_profile.values():
        row = [profile_stats.label]
        if with_status:
            row.append("[green]done[/]" if profile_stats.done else "[dim]reading…[/]")
        row += [
            str(profile_stats.urls_read),
            str(profile_stats.queued_for_submit),
            str(profile_stats.submitted),
        ]
        table.add_row(*row)
    return table


def _skip_reasons_line(stats: RunStats) -> str:
    if not stats.skip_reasons:
        return "[dim]no local skips[/]"
    return "  ".join(
        f"{kind}: {count}" for kind, count in stats.skip_reasons.most_common()
    )


def render_dashboard(stats: RunStats, progress: Progress) -> RenderableType:
    """Compose the live monitoring view."""
    events = "\n".join(stats.events) or "[dim]no events yet[/]"
    return Group(
        _header(stats),
        progress,
        Panel(_counters_table(stats), title="status", title_align="left"),
        Panel(
            _profiles_table(stats, with_status=True),
            title="profiles",
            title_align="left",
        ),
        Panel(_skip_reasons_line(stats), title="local skips", title_align="left"),
        Panel(events, title="recent events", title_align="left"),
    )


def print_summary(console: Console, stats: RunStats, *, interrupted: bool) -> None:
    """Print the end-of-run summary."""
    if interrupted:
        console.print(
            "\n[yellow]Run interrupted[/] — progress is saved; "
            "re-run to resume where it left off."
        )
    else:
        console.print("\n[green]Run complete.[/]")
    table = Table(box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("history URLs read", str(stats.urls_read_total))
    table.add_row("unique URLs", str(stats.unique_urls))
    table.add_row("URLs submitted", str(stats.submitted))
    table.add_row("new pages queued", str(stats.accepted))
    table.add_row("revisits", str(stats.revisits))
    table.add_row("server-blacklisted", str(stats.blacklisted))
    table.add_row("server-rejected", str(stats.rejected))
    table.add_row("skipped (rules)", str(stats.skipped))
    table.add_row("already submitted", str(stats.already_submitted))
    table.add_row("previously rejected", str(stats.previously_rejected))
    table.add_row("retries", str(stats.retries))
    table.add_row("errors", str(stats.errors))
    table.add_row("indexed so far", str(stats.indexed))
    table.add_row("dead", str(stats.dead))
    table.add_row("elapsed", f"{stats.elapsed_seconds:.0f}s")
    table.add_row("throughput", f"{stats.throughput:.1f}/s")
    console.print(table)
    for kind, count in stats.skip_reasons.most_common():
        console.print(f"  [dim]skipped by {kind}: {count}[/]")
    if stats.errors:
        console.print(
            "[yellow]Errors occurred; watermarks were not advanced so those URLs "
            "will be retried on the next run.[/]"
        )
    remaining = stats.total_to_submit - stats.processed
    if remaining > 0:
        console.print(f"[yellow]{remaining} URLs left in the queue.[/]")


def print_dry_run_report(
    console: Console, stats: RunStats, submissions: list["UrlSubmission"]
) -> None:
    """Print what an import run would do, without submitting anything."""
    console.print("\n[bold]Dry run[/] — nothing was submitted.\n")
    table = Table(box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("history URLs read", str(stats.urls_read_total))
    table.add_row("unique URLs", str(stats.unique_urls))
    table.add_row("would submit", str(stats.total_to_submit))
    table.add_row("already submitted", str(stats.already_submitted))
    table.add_row("previously rejected", str(stats.previously_rejected))
    table.add_row("skipped by rules", str(stats.skipped))
    console.print(table)
    for kind, count in stats.skip_reasons.most_common():
        console.print(f"  [dim]skipped by {kind}: {count}[/]")
    console.print(_profiles_table(stats))
    if submissions:
        console.print("\n[bold]Newest URLs that would be submitted:[/]")
        for submission in submissions[:10]:
            console.print(f"  {submission.url}")
        if len(submissions) > 10:
            console.print(f"  [dim]... and {len(submissions) - 10} more[/]")
