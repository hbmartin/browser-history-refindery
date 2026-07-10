"""Re-poll recorded pages and refresh their indexing status."""

import asyncio
from pathlib import Path

import httpx2
import typer
from rich.console import Console
from rich.table import Table

from browser_history_refindery.api_client import RefinderyClient, ServerError
from browser_history_refindery.config import AppConfig, load_or_create
from browser_history_refindery.logsetup import configure_logging
from browser_history_refindery.state import StateStore

_SWEEP_BATCH = 500
_INTER_REQUEST_SLEEP = 0.1


async def _sweep(config_path: Path, console: Console) -> None:
    config, _ = load_or_create(config_path)
    configure_logging(config.logging)
    async with StateStore(config.state.db_path) as state:
        page_ids = await state.nonterminal_pages(limit=_SWEEP_BATCH)
        if not page_ids:
            console.print("no pages awaiting a terminal status.")
        else:
            await _poll_pages(page_ids, config=config, state=state, console=console)
        counts = await state.status_counts()
    table = Table(title="Submissions by status")
    table.add_column("status", style="cyan")
    table.add_column("count", justify="right")
    for status, count in counts.items():
        table.add_row(status, str(count))
    console.print(table)


async def _poll_pages(
    page_ids: list[str],
    *,
    config: AppConfig,
    state: StateStore,
    console: Console,
) -> None:
    updated = 0
    async with RefinderyClient(
        base_url=config.server.base_url,
        auth_token=config.server.resolve_token(),
        request_timeout=config.server.request_timeout,
        ready_timeout=config.server.ready_timeout,
    ) as client:
        with console.status(f"polling {len(page_ids)} pages..."):
            for page_id in page_ids:
                try:
                    status = await client.page_status(page_id)
                except (httpx2.HTTPError, ServerError):
                    continue
                await state.update_page_status(
                    page_id=page_id,
                    status=status.status,
                    last_error=status.last_error,
                )
                updated += 1
                await asyncio.sleep(_INTER_REQUEST_SLEEP)
    console.print(f"updated {updated} of {len(page_ids)} pending pages.")


def run(*, config_path: Path) -> None:
    """Entry point for ``refindery-import status-sweep``."""
    console = Console()
    try:
        asyncio.run(_sweep(config_path, console))
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
