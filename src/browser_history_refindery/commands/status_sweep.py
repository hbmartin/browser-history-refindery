"""Re-poll recorded pages and refresh their indexing status."""

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from browser_history_refindery.api_client import (
    RefinderyClient,
    RequiresBatchApiError,
)
from browser_history_refindery.api_models import (
    MAX_STATUS_BATCH,
    PageStatusBatchFoundResult,
)
from browser_history_refindery.config import AppConfig, load_or_create
from browser_history_refindery.logsetup import configure_logging
from browser_history_refindery.state import StateStore

_SWEEP_BATCH = MAX_STATUS_BATCH


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
        await client.wait_ready()
        if not client.supports_batch_status:
            raise RequiresBatchApiError("batch_status")
        with console.status(f"polling {len(page_ids)} pages..."):
            results = await client.page_status_batch(page_ids)
            for result in results:
                if not isinstance(result, PageStatusBatchFoundResult):
                    continue
                await state.update_page_status(
                    page_id=result.page_id,
                    status=result.status,
                    last_error=result.last_error,
                )
                updated += 1
    console.print(f"updated {updated} of {len(page_ids)} pending pages.")


def run(*, config_path: Path) -> None:
    """Entry point for ``refindery-import status-sweep``."""
    console = Console()
    try:
        asyncio.run(_sweep(config_path, console))
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
