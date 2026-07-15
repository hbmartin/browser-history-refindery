# Frequently asked questions

These answers cover normal operation and recovery. For error-specific fixes,
see [troubleshooting](troubleshooting.md).

## Reliability and resumption

### What happens if an import is interrupted or killed?

The importer saves each completed submission outcome immediately. Its in-memory
queue is disposable: if the process stops, run the importer again with the same
state database and it rebuilds the remaining work from browser history.

The exact shutdown behavior depends on how the process stops:

| Stop | What happens |
| --- | --- |
| First ++ctrl+c++ | Requests a graceful stop. The in-flight HTTP request may finish and be recorded; queued work remains unsent. |
| Second ++ctrl+c++ | Cancels the pipeline tasks without waiting for the in-flight request. |
| Handled application error | Finalizes the local run as interrupted and leaves its watermarks unchanged. |
| `SIGTERM`, `SIGKILL`, crash, or power loss | Cannot run normal cleanup. Already committed outcomes survive, but the run ledger may retain an unfinished row. |

A controlled interrupted run does not advance profile watermarks. An abrupt
stop therefore normally causes the next run to reread a wider history window;
the submissions table filters out completed URLs. If the process stops in the
small finalization window after all submissions finish, some already-safe
profile watermarks may have been committed. This does not hide unsent work.

See [Interrupt safely](../guide/importing.md#interrupt-safely) for the normal
shutdown procedure.

### Can an interrupted request be submitted twice?

Yes, in one narrow case. Refindery can accept a batch immediately before the
importer is killed, leaving no opportunity to save the response locally. The
next run cannot distinguish that from a request that never reached the server,
so it submits the unrecorded URL again. Refindery should return the known URL as
a revisit instead of creating another page.

This is **at-least-once delivery**, not exactly-once delivery. Every outcome
that reached the local submissions table is deduplicated on later runs. Batch
outcomes are saved one item at a time, so an abrupt stop can also leave only
part of a completed batch recorded.

### How do I resume an import?

Run the same import command again from the same working directory, or pass the
same `--config` whose `[state].db_path` selects the original state database.
Use the same profile selection if you want to continue the same scope.

You do not need `--full` to resume. Old watermarks cause unsent candidates to
be read again, while the submissions table prevents completed URLs from being
sent again.

### What happens if Refindery becomes unavailable mid-import?

Transport failures and unexpected server responses are retried with adaptive
backoff up to `pacing.max_attempts`. If a URL exhausts that budget, the run
reports an error and withholds watermarks so the URL can be discovered again.
Restore Refindery, then rerun the import.

At startup, the importer waits up to `server.ready_timeout` for Refindery's
readiness endpoint. Authentication and incompatible-server errors stop the run
instead of retrying individual URLs. See
[troubleshooting](troubleshooting.md#server-not-ready-after-the-timeout).

### Why can the command finish while pages are still queued or indexing?

Ingest acceptance and page indexing are separate. The importer polls recorded
page IDs for up to `poller.drain_grace` after submissions finish, but it does
not wait indefinitely. Refresh remaining nonterminal pages later with:

```console
$ refindery-import status-sweep
```

Use the same config and state database. See
[Status and deletion](../guide/status-and-deletion.md#refresh-indexing-status).

## Browser access and local state

### Can I run the importer while my browsers are open?

Yes. The importer copies each browser history database and any existing
WAL/SHM sidecars to a temporary directory, then reads that snapshot without
modifying the live database. Visits written after the snapshot are eligible for
a later incremental run.

Safari still requires Full Disk Access for the application that launches the
importer. See [browser compatibility](browsers.md#safe-database-access).

### Do I need to keep `refindery_state.sqlite3`?

Yes. The state database is the source of truth for submission deduplication,
resume behavior, page IDs and statuses, skip reasons, and profile watermarks.
Keep using one stable, private state path and include its `-wal` and `-shm`
sidecars as active state. Stop the importer before copying the database for a
backup.

Deleting or replacing it starts an independent local history. The importer can
reread and resubmit URLs that Refindery already knows, and it loses the page IDs
needed for local status polling. See the [`[state]` configuration](configuration.md#state).

### Can two imports run at the same time?

Do not run concurrent imports that share a state database. There is no
single-import process lock: two processes can plan the same URL before either
records its outcome, causing avoidable revisit requests and last-writer-wins
submission metadata.

Separate state databases do not share deduplication state, so concurrent runs
with different state files can also submit the same URLs to Refindery. Run one
import at a time for a given history scope and backend.

### Does `--full` start over?

No. `--full` ignores profile read watermarks, but the submissions table still
deduplicates known URLs. Use it to rescan browser history, not to create a new
import identity. See [Incremental and full reads](../guide/importing.md#incremental-and-full-reads).

### Does a dry run leave files behind?

It can. A dry run may create `config.toml`, initialize or migrate the state
database, add a run row, record local exclusion results, and cache a
configuration-aware estimation profile for the selected Refindery server. It
does not ingest pages, record successful submissions, or advance profile
watermarks.

### Is there an offline mode?

There is no separate `--offline` mode. `list-profiles` is local-only, but
`--dry-run` is not: every non-empty dry run probes Refindery and sends eligible
page metadata when the server advertises live estimation and a token is
available. Refindery may fetch candidate pages to estimate them.

A real import must reach a Refindery backend. Pointing `server.base_url` at
`127.0.0.1` keeps importer-to-backend traffic on the local machine, but
Refindery generally still needs network access to fetch and index the submitted
pages. When dry-run estimation is unavailable, the importer uses the cached
profile or reports unavailable totals; there is no flag that suppresses the
initial readiness probe.

## Privacy and backend behavior

### What data leaves the Mac?

Only eligible page metadata is sent: the complete URL, browser title when
available, visit timestamps and counts, browser/profile source information, and
the machine hostname. Complete URLs include query strings and fragments, which
can contain sensitive data.

The importer does not upload browser databases, cookies, passwords, saved page
bodies, or downloaded HTML. Review [Privacy and exclusions](../guide/privacy.md)
and run `--dry-run` before a real import.

### Does the importer download and index each page?

The importer itself sends URL metadata to Refindery rather than downloading
page bodies. Refindery fetches and extracts the current page using its own
network access and policies. A real import also persists and indexes it; a dry
run may fetch, extract, and chunk it only to estimate resources. A page can
therefore differ from the version that existed when the browser visit was
recorded, or be unavailable by the time Refindery fetches it.

### Why are fewer URLs submitted than my browser reports?

Browser counts include URLs that may be merged across profiles, already present
in local submission state, permanently rejected, server-blacklisted, or removed
by local privacy rules. The dry-run and end-of-run reports separate these
outcomes. See [How work is planned](../guide/importing.md#how-work-is-planned)
and [Privacy and exclusions](../guide/privacy.md).
