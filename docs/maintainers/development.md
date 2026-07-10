# Development

The project targets Python 3.13 or newer, uses `uv` for dependency management,
and follows strict Ruff linting and typing rules.

## Set up the repository

```console
$ git clone https://github.com/hbmartin/browser-history-refindery.git
$ cd browser-history-refindery
$ uv sync --all-groups --locked
```

Run the source checkout through the project environment:

```console
$ uv run refindery-import --help
$ uv run refindery-import list-profiles
```

Local `config.toml`, `refindery_state.sqlite3*`, and generated `site/` output are
ignored. Never add a real bearer token or browsing state to fixtures or commits.

## Source organization

| Area | Responsibility |
| --- | --- |
| `cli.py` and `commands/` | Typer interfaces, console setup, and command-level error handling. |
| `browsers/` | Discovery, snapshot copying, timestamp conversion, and schema readers. |
| `pipeline.py` | Materialized plan, task orchestration, shutdown, and outcome handling. |
| `api_client.py` and `api_models.py` | Typed Refindery HTTP boundary. |
| `config.py` | Pydantic configuration and generated TOML template. |
| `filters.py` and `filter_categories.py` | Ordered local exclusions. |
| `state.py` | Async SQLite state, migrations, deduplication, and watermarks. |
| `pacer.py`, `stats.py`, and `ui.py` | Adaptive timing, counters, and Rich rendering. |

See [architecture and backend contract](architecture.md) for data flow and
correctness invariants.

## Required checks

Run every check after a change:

```console
$ uv run ruff check .
$ uv run ruff format --check .
$ uv run pytest
$ uv run ty check
$ uv run pyrefly check
$ uv run lizard src
```

Tests use `pytest-asyncio` and `httpx2-pytest`. HTTP responses are single-use by
default; mark mocks reusable for repeated or concurrent calls and optional when
a branch may legitimately avoid the endpoint.

## Build the documentation

Install only the locked docs group when no development environment is needed:

```console
$ uv sync --only-group docs --locked
$ uv run --no-sync zensical build --clean --strict
```

For live authoring:

```console
$ uv run --only-group docs zensical serve
```

`zensical.toml` defines explicit navigation. New pages must be added there or
they will be omitted from the published navigation. Strict builds must pass
before merge.

The Pages workflow builds `site/` on pushes to `main` and on manual dispatch.
Repository owners must configure **Settings → Pages → Build and deployment →
GitHub Actions**; the workflow does not enable Pages through the API.

## Documentation contract

Keep these sources synchronized when public behavior changes:

- `reference/cli.md` for commands, options, confirmation, and exit behavior;
- `reference/configuration.md` and the `DEFAULT_CONFIG_TOML` template for every
  config key and default;
- `reference/browsers.md` for discovery paths and reader support;
- `guide/privacy.md` for payload fields and filter behavior; and
- `maintainers/architecture.md` for the backend endpoints and response shapes.

The documentation is curated Markdown. Do not expose internal modules through
generated API pages unless the project deliberately creates a stable Python
API.

## Reader changes

Browser SQLite schemas and timestamp epochs are easy to misinterpret. Add
fixture-backed reader tests and UTC golden tests for every new browser family or
query change. Preserve the snapshot-first rule so tests and production use the
same access model.

## State changes

Increment `SCHEMA_VERSION` when persisted shape or semantics change. Add an
in-place migration from every supported older schema and a test that a newer
unknown schema is refused. Deduplication must remain correct even when a run is
limited, interrupted, or partially fails.

## Backend changes

Validate new request and response data with Pydantic at the HTTP boundary. Add
client tests for every status code and pipeline tests for persistence,
statistics, retries, and watermark behavior. Treat authentication and deletion
messages as user-facing interfaces.
