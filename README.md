# browser-history-refindery

Ingest your macOS browser history into a Refindery backend.

`refindery-import` discovers every browser history database on the machine —
Safari, Google Chrome, Firefox, Comet, Dia, and any other Chromium fork
(Arc, Brave, Edge, ...) with per-profile granularity — lets you multi-select
which profiles to import, and submits each URL to Refindery's
`POST /v1/pages` endpoint (URL-only mode; the server fetches and extracts
content itself).

## Features

- **Multi-browser, multi-profile discovery** with the browser's own profile
  display names (from Chromium `Local State`, Firefox `profiles.ini`).
- **Copy-then-read**: history databases are snapshotted (including WAL/SHM
  sidecars) so running browsers are never touched.
- **Global dedup**: each URL is submitted once per run, with visit counts and
  per-browser sightings merged into the page metadata.
- **Resumable and incremental**: progress is tracked in a local SQLite
  database; interrupted runs resume, and later runs only pick up visits newer
  than each profile's high-water mark.
- **Adaptive, queue-aware pacing**: submissions back off exponentially on
  errors and slow down when the server's pending-job backlog grows.
- **Client-side exclusions**: non-http(s) schemes, localhost / RFC-1918 /
  `.local` hosts, an editable sensitive-category blocklist (banking, health,
  auth & webmail on by default; adult available but off), plus your own
  domain/pattern skip rules. Every skip is recorded with the rule it matched.
- **Live dashboard**: progress bar, per-profile counters, indexing status,
  current pacing interval, and recent events while the run proceeds.

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run refindery-import list-profiles   # see what's discoverable
```

On first run a commented `config.toml` is created next to where you run the
tool. Set the auth token there (`server.auth_token`) or export
`REFINDERY_AUTH_TOKEN`.

> **Safari note:** reading `~/Library/Safari/History.db` requires granting
> Full Disk Access to your terminal app (System Settings → Privacy &
> Security → Full Disk Access), then restarting the terminal.

## Usage

```bash
# Interactive import: pick profiles, watch the live dashboard
uv run refindery-import

# Non-interactive variants
uv run refindery-import import --all              # every discovered profile
uv run refindery-import import --dry-run --all    # read + filter, submit nothing
uv run refindery-import import --limit 100        # cap this run
uv run refindery-import import --full             # ignore incremental watermarks
uv run refindery-import import --db path/to/History --browser chromium

# Track pages that were still indexing when a run ended
uv run refindery-import status-sweep

# Remove content from the index (destructive) and manage the blacklist
uv run refindery-import forget https://example.com/private --reason "oops"
uv run refindery-import forget example.com --domain
uv run refindery-import blacklist list
uv run refindery-import blacklist remove bl_abc123
```

## Configuration

All knobs live in `config.toml` (auto-created with commented defaults):
server URL/token/timeouts, pacing (base interval, floor/ceiling, backoff and
recovery factors, retry budget, backlog polling), status poller cadence,
exclusion categories and custom skip rules, and the state database path. See
the generated file for the full annotated list.

## How it works

1. Read every selected profile's history from a temporary snapshot copy,
   aggregated per URL (visit count, first/last visit).
2. Merge across profiles, drop URLs already submitted in earlier runs, apply
   exclusion rules (each skip recorded with its reason).
3. Submit newest-first through the adaptive pacer, recording `202 queued`,
   `200 revisit`, or `403 blacklisted` per URL in `refindery_state.sqlite3`.
4. A background poller tracks submitted pages to `indexed`/`dead`; whatever
   is still pending when the run ends can be swept later with `status-sweep`.
5. Watermarks advance only after a clean, error-free run, so nothing is ever
   silently lost.

## Development

```bash
uv run ruff check && uv run ruff format --check
uv run pytest
uv run ty check && uv run pyrefly check
uv run lizard src
```
