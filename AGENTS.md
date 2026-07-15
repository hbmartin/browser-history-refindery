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
- `pipeline.run_import` orchestrates a TaskGroup of streaming producer + submitter + status poller + backlog watcher. Unbounded imports enqueue each profile while later profiles are still read; `--limit` fully materializes all profiles for a global newest-first cutoff. A separate `rich` Live refresh task is cancelled after the group exits.
- Correctness rule: the `submissions` table in the local state DB is the source of truth for dedup/resume; per-profile watermarks are only an optimization and advance only after a clean, error-free run.
- State schema v4 stores `last_visit_at` for each submission, run-level rejected counts, and one validated dry-run estimation fallback profile per normalized Refindery base URL; older databases backfill visit times from `submitted_at`, add the rejected counter, and create the profile cache during migration.
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
- Streaming submissions must persist the same immutable snapshot used to build the HTTP request; the shared merge object can gain later-profile visits while the request is in flight.
- With revisit resubmission enabled, a URL successfully sent before a later-profile merge must be queued again for the newer visit; otherwise advancing that profile's watermark can hide the unsent revisit on the next run.
- A shared URL classified as already submitted must be reconsidered if a later profile raises its merged visit time past the stored submission time and revisit resubmission is enabled.
- UI helpers that only read URLs accept a structural `UrlDisplay` protocol and a read-only `Sequence`; this keeps tests and callers type-safe without coupling presentation code to pipeline models.
- When a test must inspect Loguru's untyped private `_core`, confine an explicit `Any` cast to that introspection point rather than suppressing an unrelated attribute diagnostic.
- Full URLs, including query strings, remain the identity used for merging, submission, and persisted state. Log displays remove query/fragment contents and replace them with a stable fingerprint so distinct URLs remain distinguishable without leaking sensitive values.
- A profile reader may yield repeated records for one full URL; merge every record, but classify and enqueue that URL only once per profile.
- Mutable streaming submissions carry explicit queue-membership state. Keep every queue transition in `_Runner._enqueue`, leave the flag set while the submitter reserves an item for pacing, and clear it only when the HTTP attempt begins so later profile merges cannot create duplicate requests.
- Queue-state tests should cover both duplicate rejection and shutdown after pacing; the latter must restore the submitter's reserved item without clearing its `queued` flag.
- Dry runs always make a one-shot readiness probe for non-empty plans and use `POST /v1/pages/estimate/batch` only when `batch_estimate` is advertised and a token is available. Estimation failures never fail the dry run: use the cached per-server fallback profile only for unresolved pages, and show unavailable totals when no profile exists.
- Estimate money values stay as `Decimal` from validated pydantic wire models through aggregation and formatting. A null total must carry `unpriced_components`; never interpret missing provider pricing as zero.

## Documentation
- The Zensical site is configured in `zensical.toml`; its curated Markdown lives under `docs/`, and generated `site/` output is gitignored.
- The explicit navigation in `zensical.toml` is authoritative. Add every published page there and keep CLI, config, browser, privacy, and backend-contract references synchronized with code changes.
- Cross-cutting operational answers belong in `docs/reference/faq.md`; keep task instructions in the guides and error remediation in troubleshooting, then cross-link instead of maintaining divergent explanations.
- The CLI has no dedicated offline mode. `list-profiles` avoids backend access, but every non-empty `--dry-run` probes Refindery and may send eligible page metadata for live estimation; dry run can also create or update local config/state records, while a real import always requires Refindery.
- Documentation is intentionally curated rather than generated from internal Python modules; package internals are not a stable public API.
- CI installs only the locked docs dependency group and builds with `uv run --no-sync zensical build --clean --strict`. GitHub Pages must be enabled with GitHub Actions in repository settings; the workflow does not enable it through the API.
- `uv run --only-group docs zensical serve` is supported by the project's current `uv`; retain the flag when documenting an isolated docs-only authoring environment.
- Once `project.markdown_extensions` is present, keep Zensical's standard extension set explicit alongside Mermaid's custom fence; otherwise syntax such as button attribute lists, admonitions, or key glyphs can render as raw Markdown.

## GitHub automation
- Core CI runs on Ubuntu with Python 3.13 and 3.14; quality and package metadata checks use Python 3.13, and the package job installs the built wheel in a clean environment before exercising the CLI.
- Stable releases use `vX.Y.Z` tags that must match both `project.version` and `browser_history_refindery.__version__`. The PyPI workflow builds without elevated permissions, then publishes from a separate `pypi` environment job using OIDC.
- Adapted workflows pin every action to an immutable commit SHA, disable checkout credential persistence, declare least-privilege permissions, and cancel stale runs where safe. Dependabot is responsible for refreshing action pins.
- `docs.yml` is owned by the documentation pipeline and was deliberately left unchanged by the GitHub automation adaptation; validate hardened workflows separately when running Zizmor for this change.
- Release Drafter excludes labeled pull requests through its root-level `exclude-labels`; `pre-exclude` is not a supported category type.
