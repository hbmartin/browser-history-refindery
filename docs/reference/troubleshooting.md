# Troubleshooting

Start with `refindery-import list-profiles` and an interactive `--dry-run`.
Those commands separate browser-reading problems from backend and token
problems. For questions about normal behavior, recovery, and local state, see
the [frequently asked questions](faq.md).

## No browser history databases found

The importer found no supported Safari directory, Firefox profile database, or
Chromium profile matching its discovery signals.

- Confirm the browser has recorded at least one history entry.
- Run the importer as the same macOS user who owns the browser profiles.
- Check the paths in [browser compatibility](browsers.md).
- Use `--db PATH --browser FAMILY` for a compatible database in a nonstandard
  location.

## Safari needs Full Disk Access

Grant access to the terminal application—not the Python executable—under
**System Settings → Privacy & Security → Full Disk Access**, then fully quit and
restart that terminal. Run `list-profiles` again.

If access remains denied, confirm the import is launched by that same terminal
and not by a different editor, launcher, or background service.

## No auth token configured

Set one of:

```toml
[server]
auth_token = "replace-with-your-token"
```

```console
$ export REFINDERY_AUTH_TOKEN="replace-with-your-token"
```

A non-empty config value wins. Confirm `--config` points to the file you edited
and run from the directory containing the expected config and state files.

## The server rejected the bearer token

HTTP `401` means a token was sent but Refindery rejected it. Check for copied
whitespace, an expired or rotated token, the selected `server.base_url`, and an
environment variable being shadowed by `server.auth_token`.

## Server not ready after the timeout

The importer repeatedly calls `GET /readyz` until `server.ready_timeout`
expires.

- Confirm Refindery is running at `server.base_url`.
- Test its readiness endpoint from the same network environment.
- Check TLS, proxy, DNS, and firewall behavior for a remote backend.
- Increase `ready_timeout` if startup legitimately takes longer.

## Import retries or becomes slow

The current interval grows after transport errors and retryable server
responses. It also grows when the observed pending-job count exceeds
`queue_depth_threshold`.

Check the dashboard's error events, backlog, and interval. Review Refindery
health before lowering delays or raising retry budgets; those changes can make
an overloaded backend worse.

## Pages remain queued or indexing

The import poller stops after `poller.drain_grace`. Run:

```console
$ refindery-import status-sweep
```

Use the same config and state database as the import. A `dead` page is terminal;
inspect its last error and the Refindery worker logs.

## A dry run changed local files

Dry run means no backend submission, not a read-only local process. The command
can create `config.toml`, initialize or migrate the state database, add a run,
record exclusion results, and cache Refindery's fallback estimation profile. A
non-empty plan also probes Refindery and may ask it to fetch candidate pages for
estimation. It does not record submissions or advance watermarks.

## `--full` did not resend old URLs

`--full` bypasses profile read watermarks only. The submissions table still
deduplicates URLs. Enable `import.resubmit_revisits` to send a URL after a newer
browser visit, or use a separate state database when intentionally starting a
completely independent import history.

Do not delete state casually: it is the source of truth for deduplication and
resumption.

## State database schema is too new

The selected database was written by a newer importer version. Upgrade
`browser-history-refindery`, or point `state.db_path` at a different file. The
older build refuses to downgrade or overwrite the newer schema.

## A URL was rejected with HTTP 422

The backend considered the request invalid. The validation detail is stored in
local state, the outcome is reported as rejected rather than a transient error,
and the URL is never retried automatically—even with revisit resubmission
enabled. Correct the incompatibility before using a fresh state database.

## A purged URL does not return

Removing a server blacklist rule allows future ingestion but does not restore
purged content. The importer also retains its local submission record. Arrange
a new eligible visit and revisit resubmission, or intentionally use a fresh
state database, then ingest the URL again.
