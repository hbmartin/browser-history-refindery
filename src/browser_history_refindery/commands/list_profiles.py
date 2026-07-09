"""List discovered browser profiles and their history sizes."""

from rich.console import Console
from rich.table import Table

from browser_history_refindery.browsers import (
    FullDiskAccessError,
    count_urls,
    discover_all,
)


def run() -> None:
    """Print a table of every discovered browser profile."""
    console = Console()
    profiles = discover_all()
    if not profiles:
        console.print("[red]no browser history databases found on this machine[/]")
        return
    table = Table(title="Discovered browser profiles")
    table.add_column("browser", style="cyan")
    table.add_column("profile")
    table.add_column("URLs", justify="right")
    table.add_column("path", style="dim", overflow="fold")
    for profile in profiles:
        try:
            urls = str(count_urls(profile))
        except FullDiskAccessError:
            urls = "[red]needs Full Disk Access[/]"
        except Exception as exc:  # noqa: BLE001 - keep listing other profiles
            urls = f"[red]unreadable ({exc})[/]"
        table.add_row(
            profile.browser_label,
            profile.profile_name,
            urls,
            str(profile.history_path),
        )
    console.print(table)
