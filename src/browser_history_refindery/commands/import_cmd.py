"""The main import command: discover, select, and run the pipeline."""

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from browser_history_refindery.browsers import (
    BrowserFamily,
    BrowserProfile,
    discover_all,
)
from browser_history_refindery.config import load_or_create
from browser_history_refindery.pipeline import run_import
from browser_history_refindery.ui import select_profiles


def _direct_profile(db_path: Path, family: BrowserFamily) -> BrowserProfile:
    return BrowserProfile(
        browser_id=f"custom-{family}",
        browser_label=f"Custom ({family})",
        profile_dir=db_path.parent.name or "custom",
        profile_name=db_path.name,
        history_path=db_path,
        family=family,
    )


def _resolve_profiles(
    console: Console,
    *,
    db_path: Path | None,
    family: BrowserFamily | None,
    select_all: bool,
) -> list[BrowserProfile]:
    if db_path is not None:
        if family is None:
            console.print("[red]--db requires --browser to name the schema family[/]")
            raise typer.Exit(code=2)
        if not db_path.is_file():
            console.print(f"[red]no such database: {db_path}[/]")
            raise typer.Exit(code=2)
        return [_direct_profile(db_path, family)]
    discovered = discover_all()
    if not discovered:
        console.print("[red]no browser history databases found on this machine[/]")
        raise typer.Exit(code=1)
    if select_all:
        return discovered
    selected = select_profiles(discovered)
    if not selected:
        console.print("nothing selected; exiting.")
        raise typer.Exit(code=0)
    return selected


def run(
    *,
    config_path: Path,
    db_path: Path | None,
    family: BrowserFamily | None,
    select_all: bool,
    limit: int | None,
    dry_run: bool,
    full: bool,
) -> None:
    """Entry point for ``refindery-import [import]``."""
    console = Console()
    config, created = load_or_create(config_path)
    if created:
        console.print(
            f"[yellow]Created {config_path} with defaults — review it (especially "
            "server.auth_token) before a real run.[/]"
        )
    profiles = _resolve_profiles(
        console, db_path=db_path, family=family, select_all=select_all
    )
    try:
        asyncio.run(
            run_import(
                config=config,
                profiles=profiles,
                console=console,
                dry_run=dry_run,
                limit=limit,
                ignore_watermarks=full,
            )
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
