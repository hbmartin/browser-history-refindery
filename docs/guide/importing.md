# Importing history

An import reads, merges, filters, and queues one browser profile at a time while
eligible URLs from earlier profiles are submitted. Background tasks monitor
Refindery throughout delivery. A limited run is different: it reads every
profile before submission so the newest URLs across the complete selection can
win the available slots.

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

## How work is planned

For each selected profile, the importer:

1. reads visits newer than the profile watermark, unless `--full` is set;
2. aggregates each URL's visit count and first and last visit timestamps;
3. merges the same URL with any matching records read so far;
4. removes terminal submissions already recorded in local state;
5. applies local exclusion rules;
6. sorts that profile's eligible URLs by most recent visit; and
7. queues them while the next profile is read.

When the same URL appears in several profiles before its request starts,
Refindery receives combined visit metadata and a per-profile `sources` list. In
an unbounded streaming run, a request can finish before a later profile reveals
another sighting. The completed request retains its original snapshot. With
`import.resubmit_revisits = true`, a newer later sighting is queued as a
revisit.

`--limit` and `--dry-run` finish reading all profiles before their final output.
A limited run globally sorts the merged candidates newest-first, applies the
limit, and then starts submission.

## Preview without submitting

`--dry-run` performs the read, merge, deduplication, filtering, and reporting
phases, then asks Refindery to estimate the eligible pages:

```console
$ refindery-import import --dry-run --all
```

The report includes:

- total eligible pages after `--limit`, when present;
- every domain and its page count, grouping lowercase hostnames after removing
  only a leading `www.`;
- incremental estimated disk storage;
- total configured indexing cost in USD and its component breakdown; and
- counts covered by live estimates, the cached fallback profile, zero-impact
  server outcomes, or no available estimate.

For non-empty plans the importer probes `GET /readyz` once. When Refindery
advertises `batch_estimate` and a bearer token is configured, the importer sends
the same page metadata as an ingest request to
`POST /v1/pages/estimate/batch`. Refindery may fetch and extract the current
page, but the estimation contract forbids persistence and paid-provider calls.

If readiness, capability, authentication, or an estimate batch fails, only the
unresolved pages use the latest configuration-aware fallback profile cached for
that `server.base_url`. Storage or cost remains explicitly unavailable when no
profile exists or a configured paid component has no price. Estimation failures
do not make the dry run exit unsuccessfully.

A dry run may create `config.toml`, migrate the local state database, record the
run and excluded URLs, and refresh the estimation-profile cache. It never
records a successful submission or advances a profile watermark.

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

For abrupt termination, the in-flight delivery window, and exact restart
behavior, see [What happens if an import is interrupted or killed?](../reference/faq.md#what-happens-if-an-import-is-interrupted-or-killed).

## Follow indexing status

While submissions are active, a poller checks recorded page IDs until they are
`indexed` or `dead`. It continues for `poller.drain_grace` seconds after the
submitter finishes. Remaining pages can be refreshed with
[`status-sweep`](status-and-deletion.md#refresh-indexing-status).
