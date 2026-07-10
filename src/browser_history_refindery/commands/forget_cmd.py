"""Forget (purge + blacklist) pages and manage server blacklist rules."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from browser_history_refindery.api_client import RefinderyClient
from browser_history_refindery.config import AppConfig, load_or_create
from browser_history_refindery.logsetup import configure_logging


def _client(config: AppConfig) -> RefinderyClient:
    return RefinderyClient(
        base_url=config.server.base_url,
        auth_token=config.server.resolve_token(),
        request_timeout=config.server.request_timeout,
        ready_timeout=config.server.ready_timeout,
    )


def _run_with_client[T](
    config_path: Path,
    console: Console,
    action: Callable[[RefinderyClient], Awaitable[T]],
) -> T:
    config, _ = load_or_create(config_path)
    configure_logging(config.logging)

    async def runner() -> T:
        async with _client(config) as client:
            return await action(client)

    try:
        return asyncio.run(runner())
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc


def forget(
    *, config_path: Path, target: str, is_domain: bool, reason: str | None
) -> None:
    """Entry point for ``refindery-import forget``. Destructive."""
    console = Console()
    kind = "domain" if is_domain else "URL"
    typer.confirm(
        f"Permanently purge {kind} '{target}' from the index and blacklist it? "
        "This cannot be undone.",
        abort=True,
    )
    response = _run_with_client(
        config_path,
        console,
        lambda client: client.forget(
            url=None if is_domain else target,
            domain=target if is_domain else None,
            reason=reason,
        ),
    )
    console.print(
        f"purged [bold]{response.pages_purged}[/] pages; blacklist rule "
        f"[cyan]{response.blacklist_id}[/] ({response.kind}: {response.pattern})"
    )


def blacklist_list(*, config_path: Path) -> None:
    """Entry point for ``refindery-import blacklist list``."""
    console = Console()
    response = _run_with_client(
        config_path, console, lambda client: client.list_blacklist()
    )
    if not response.entries:
        console.print("no blacklist rules.")
        return
    table = Table(title="Server blacklist rules")
    table.add_column("id", style="cyan")
    table.add_column("kind")
    table.add_column("pattern")
    table.add_column("reason", style="dim")
    table.add_column("created", style="dim")
    for entry in response.entries:
        table.add_row(
            entry.id, entry.kind, entry.pattern, entry.reason or "", entry.created_at
        )
    console.print(table)


def blacklist_remove(*, config_path: Path, blacklist_id: str) -> None:
    """Entry point for ``refindery-import blacklist remove``."""
    console = Console()

    async def action(client: RefinderyClient) -> None:
        await client.remove_blacklist(blacklist_id)

    _run_with_client(config_path, console, action)
    console.print(
        f"removed rule [cyan]{blacklist_id}[/] — future ingests of that target are "
        "allowed again (purged content stays purged)."
    )
