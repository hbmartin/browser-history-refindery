## Workflow

Always run ruff and pytest and ty and pyrefly and lizard (with uv) after making any changes

## Development Notes

- The project supports Python 3.13+
- Uses uv for dependency management instead of traditional pip/setuptools
- Code style uses Ruff formatter and linter

## Python Practices
- Always use or add type hints
- Prefer @dataclasses where applicable
- Always use f-string over string formatting or concatentation (except in logging strings)
- Use async generators and comprehensions when they might provide benefits
- Use underscores in large numeric literals
- Use walrus assignment := where applicable
- Prefer to use named arguments when calling a method with more than one argument
- Use "list" instead of "List" and "dict" instead of "Dict" and "|" instead of "Union" for types
- Use "Self" for applicable types
- Use Structural Pattern Matching (match...case) where applicable
- Always use pathlib.Path for file operations, never use os.path
- Inputs (e.g. HTTP requests) and call results (e.g. HTTP requests not already wrapped in a library) must be validated and made type safe with pydantic.

# Update AGENTS.md
Update AGENTS.md with notes, learnings, findings, or other useful patters you have learned

# Notes

## Architecture (refindery-import)
- Entry point: `refindery-import = browser_history_refindery.cli:app` (Typer). Running with no subcommand starts a default interactive import via the app callback.
- `pipeline.run_import` is the orchestrator: fully-materializing producer (read → merge → dedup → filter), then an asyncio TaskGroup of submitter + status poller + backlog watcher, with a `rich` Live dashboard refreshed by a separate task that is cancelled after the group exits (avoids termination coordination).
- Correctness rule: the `submissions` table in the local state DB is the source of truth for dedup/resume; per-profile watermarks are only an optimization and advance only after a clean, error-free run.
- State schema v3 stores `last_visit_at` for each submission and run-level rejected counts; older databases backfill visit times from `submitted_at` and add the rejected counter during migration.
- Browser readers are sync (stdlib sqlite3 over a tempdir snapshot copy of the DB + WAL/SHM) called via `asyncio.to_thread`; the state store uses aiosqlite.
- Epoch conversions: Chromium = µs since 1601-01-01 (offset 11_644_473_600 s from Unix), Firefox = µs since Unix epoch, Safari = float seconds since 2001-01-01 (offset 978_307_200 s). Golden tests in `tests/test_times.py`.

## Gotchas / learnings
- HTTP tests use `httpx2-pytest`'s `httpx2_mock` fixture so the old `httpx` package is not retained through `respx`. Responses are single-use unless registered with `is_reusable=True`; concurrent or dry-run endpoints that may not be called need `is_optional=True`.
- For `httpx2_mock` query assertions, constrain callbacks with an anchored regex matching the URL path plus an optional query string, then compare `dict(request.url.params)` inside the callback. A literal path-only URL matcher does not match requests containing a query string.
- Readiness polling tests should register a transport exception followed by a successful response to cover retry-and-recovery behavior, not only successful responses and HTTP timeout responses.
- In readiness polling, map expected transport failures to an explicit `is_ready = False`; this documents the intentional retry behavior and avoids empty exception handlers.
- ruff `ASYNC109` bans a `timeout` parameter on async functions — pass timeouts via constructor/config instead.
- ruff `TRY003`/`EM102`: put exception messages inside custom exception classes' `__init__` (e.g. `FullDiskAccessError`, `AuthError`) instead of at raise sites.
- Safari titles live on `history_visits`, not `history_items`; the reader uses an explicit newest-visit subquery while separately aggregating `MIN` and `MAX` visit times.
- When `--limit` actually truncates the eligible queue, the plan clears all candidate watermarks; the submissions table still deduplicates completed URLs on the next run without hiding unsent ones.
- Exceptions raised inside `asyncio.TaskGroup` emerge as an `ExceptionGroup`; `_run_tasks` unwraps runtime-only groups to their first application error so command-level remediation still works.
- Read-only SQLite URIs must start from `Path.resolve().as_uri()` before appending `?mode=ro`; interpolating a raw path into `file:{path}` misparses custom database names containing `#` or `?`.
- Permanent HTTP 422 responses are persisted in `submissions` with their validation message, counted as both sent and rejected, and excluded from `errors`; rejected URLs remain terminal even when `resubmit_revisits` is enabled, while clean runs still advance watermarks.
- Chromium-fork discovery keys on BOTH `Local State` containing `profile.info_cache` AND a profile dir with a SQLite-magic `History` file (check magic bytes, don't open — avoids locks and Electron false positives). Dia nests under `Dia/User Data/`.
- Safari's `~/Library/Safari` dir existence check passes without Full Disk Access; permission errors surface at snapshot-copy time and are mapped to `FullDiskAccessError` with remediation text.
- Typer commands use keyword-only params (`*,`) so ruff FBT rules stay happy; `import` is a keyword so the command function is `import_` registered with `name="import"`.
- `config.toml` and `refindery_state.sqlite3*` are gitignored (the config holds the bearer token).
- The default-config template is a hand-commented string constant (`DEFAULT_CONFIG_TOML`) kept in sync with the pydantic defaults — `test_first_run_writes_template` verifies it parses back to `AppConfig()`.
- `BrowserProfile.key` is the readable `browser_id:profile_dir` stats key and can collide across distinct history paths; use the path-aware `BrowserProfile.watermark_key` for persisted watermark lookups and `--limit` pruning. Watermark tests should use same-family profiles with the same profile directory name to cover collisions explicitly.
- Persisted path-based identities must normalize with `Path.resolve()` before stringification so equivalent relative and absolute history paths cannot create duplicate watermark rows.
- Safari incremental reads limit visit aggregates to the post-watermark window, but title fallback must search the URL's full visit history so an untitled revisit can reuse an older title.
