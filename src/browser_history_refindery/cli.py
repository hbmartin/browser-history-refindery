"""Typer CLI for refindery-import."""

from pathlib import Path
from typing import Annotated

import typer

from browser_history_refindery import __version__
from browser_history_refindery.browsers import BrowserFamily
from browser_history_refindery.commands import (
    forget_cmd,
    import_cmd,
    list_profiles,
    status_sweep,
)
from browser_history_refindery.config import DEFAULT_CONFIG_PATH

app = typer.Typer(
    name="refindery-import",
    help=(
        "Ingest browser history into a Refindery backend. "
        "Running with no subcommand starts an import with default options."
    ),
    no_args_is_help=False,
)
blacklist_app = typer.Typer(help="Manage server-side blacklist rules.")
app.add_typer(blacklist_app, name="blacklist")

ConfigOption = Annotated[
    Path, typer.Option("--config", help="Path to the TOML config file.")
]


def _version_callback(*, value: bool) -> None:
    if value:
        typer.echo(f"refindery-import {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    *,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=lambda value: _version_callback(value=value),
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Run an import with default options when no subcommand is given."""
    del version
    if ctx.invoked_subcommand is None:
        import_cmd.run(
            config_path=DEFAULT_CONFIG_PATH,
            db_path=None,
            family=None,
            select_all=False,
            limit=None,
            dry_run=False,
            full=False,
        )


@app.command(name="import")
def import_(
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Import from one specific history database."),
    ] = None,
    browser: Annotated[
        BrowserFamily | None,
        typer.Option("--browser", help="Schema family of the --db database."),
    ] = None,
    all_profiles: Annotated[
        bool,
        typer.Option("--all", help="Import every discovered profile, no prompt."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Submit at most this many URLs."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Build the plan and request resource estimates, but submit nothing.",
        ),
    ] = False,
    full: Annotated[
        bool,
        typer.Option("--full", help="Ignore incremental watermarks; re-read all."),
    ] = False,
) -> None:
    """Import browser history into Refindery."""
    import_cmd.run(
        config_path=config,
        db_path=db,
        family=browser,
        select_all=all_profiles,
        limit=limit,
        dry_run=dry_run,
        full=full,
    )


@app.command(name="list-profiles")
def list_profiles_() -> None:
    """List discovered browsers, profiles, and history sizes."""
    list_profiles.run()


@app.command(name="status-sweep")
def status_sweep_(*, config: ConfigOption = DEFAULT_CONFIG_PATH) -> None:
    """Re-poll recorded pages and refresh their indexing status."""
    status_sweep.run(config_path=config)


@app.command()
def forget(
    target: Annotated[str, typer.Argument(help="URL (or domain with --domain).")],
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    domain: Annotated[
        bool,
        typer.Option("--domain", help="Treat TARGET as a whole domain."),
    ] = False,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Note stored on the rule.")
    ] = None,
) -> None:
    """Purge a URL or domain from the index and blacklist it. Destructive."""
    forget_cmd.forget(
        config_path=config, target=target, is_domain=domain, reason=reason
    )


@blacklist_app.command(name="list")
def blacklist_list(*, config: ConfigOption = DEFAULT_CONFIG_PATH) -> None:
    """List server-side blacklist rules."""
    forget_cmd.blacklist_list(config_path=config)


@blacklist_app.command(name="remove")
def blacklist_remove(
    blacklist_id: Annotated[str, typer.Argument(help="Rule id (bl_...).")],
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
) -> None:
    """Remove one blacklist rule so future ingests are allowed again."""
    forget_cmd.blacklist_remove(config_path=config, blacklist_id=blacklist_id)


if __name__ == "__main__":
    app()
