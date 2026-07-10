# Importing history

An import has two phases: first the complete submission plan is built locally,
then eligible URLs are delivered newest-first while background tasks monitor
Refindery.

## Select input profiles

Running `refindery-import` or `refindery-import import` opens an interactive
multi-select list. Every discovered profile starts checked. Deselect anything
you do not want to read.

Use `--all` for automation:

```console
$ refindery-import import --all
```

Use `list-profiles` before unattended imports to confirm what `--all` includes.

## Import a specific database

You can bypass discovery and read one compatible SQLite database. Supply both
its path and schema family:

```console
$ refindery-import import \
    --db "/path/to/History" \
    --browser chromium
```

Valid families are `chromium`, `firefox`, and `safari`. `--db` without
`--browser`, or a path that is not a file, exits with usage status `2`.

The file is still copied to a temporary directory before it is opened. The
importer does not modify the source database.

## How a plan is built

For each selected profile, the importer:

1. reads visits newer than the profile watermark, unless `--full` is set;
2. aggregates each URL's visit count and first and last visit timestamps;
3. merges the same URL across all selected profiles;
4. removes terminal submissions already recorded in local state;
5. applies local exclusion rules; and
6. sorts eligible URLs by most recent visit, newest first.

When the same URL appears in several profiles, Refindery receives one request
with combined visit metadata and a per-profile `sources` list.

## Preview without submitting

`--dry-run` performs the read, merge, deduplication, filtering, sorting, and
reporting phases without resolving a bearer token or contacting Refindery:

```console
$ refindery-import import --dry-run --all
```

It may still create `config.toml` and the local state database and record the
run and excluded URLs. It never records a successful submission or advances a
profile watermark.

## Limit a run

Use `--limit` to submit only the newest eligible URLs:

```console
$ refindery-import import --all --limit 500
```

The limit applies after merging, deduplication, and exclusion. If it truncates
a profile's eligible URLs, that profile's watermark is not advanced. The next
run rereads its candidate window, and the submissions table prevents completed
URLs from being sent twice.

## Incremental and full reads

After a clean run with no submission errors, the importer advances each
profile's watermark to the newest visit it read. Later runs ask the browser
reader only for newer visits.

Use `--full` to ignore those read watermarks:

```console
$ refindery-import import --all --full
```

`--full` rereads browser history, but it does not disable submission
deduplication. With the default `import.resubmit_revisits = false`, URLs already
recorded in the submissions table are still skipped.

Set `import.resubmit_revisits = true` to submit a known URL again when its
latest browser visit is newer than the visit represented by its prior
submission. Permanently rejected `422` requests are never retried
automatically.

## Submission outcomes

Each eligible URL is handled as one of these outcomes:

| HTTP response | Dashboard outcome | Local behavior |
| --- | --- | --- |
| `202 Accepted` | New page queued | Store its page ID and poll indexing status. |
| `200 OK` | Revisit | Store the known page ID and current status. |
| `403 Forbidden` | Server-blacklisted | Record a terminal blacklist outcome. |
| `422 Unprocessable Content` | Rejected | Store the validation message and never retry automatically. |
| Transport error or unexpected response | Error/retry | Back off and retry up to `pacing.max_attempts`. |

If a URL exhausts its retry budget, the run reports an error and does not
advance watermarks. That candidate can be discovered again on a later run.

## Adaptive pacing

The submitter waits between requests. Successful requests gradually return the
interval toward `pacing.floor`; transport and server failures multiply it by
`pacing.backoff_factor`, up to `pacing.ceiling`.

A background watcher polls Refindery's pending-job queue. When it exceeds
`pacing.queue_depth_threshold`, the effective interval is multiplied by
`pacing.queue_slowdown_factor`. The dashboard displays the current interval and
observed backlog.

## Interrupt safely

Press ++ctrl+c++ once to request a graceful stop. The in-flight request is
allowed to finish, queued work remains unsent, and completed outcomes stay in
the local state database. Run the importer again to resume.

Press ++ctrl+c++ a second time to force cancellation. An interrupted run never
advances profile watermarks.

## Follow indexing status

While submissions are active, a poller checks recorded page IDs until they are
`indexed` or `dead`. It continues for `poller.drain_grace` seconds after the
submitter finishes. Remaining pages can be refreshed with
[`status-sweep`](status-and-deletion.md#refresh-indexing-status).
