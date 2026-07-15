# CLI reference

The executable is `refindery-import`. Running it with no subcommand starts the
default interactive import using `config.toml` in the current directory.

## Global command

```console
$ refindery-import [--version] [--help]
```

| Option | Behavior |
| --- | --- |
| `--version` | Print `refindery-import VERSION` and exit successfully. |
| `--install-completion` | Install shell completion for the current shell. |
| `--show-completion` | Print the completion script for the current shell. |
| `--help` | Show global help and available subcommands. |

The global invocation does not accept `--config`. Use the explicit `import`
subcommand when selecting another config file.

## `import`

```console
$ refindery-import import [OPTIONS]
```

Read, merge, filter, and submit browser history.

| Option | Value | Default | Behavior |
| --- | --- | --- | --- |
| `--config` | Path | `config.toml` | Select the TOML config file. |
| `--db` | Path | Discovery | Read one specific history database. |
| `--browser` | `chromium`, `firefox`, or `safari` | None | Declare the schema family used by `--db`; required with it. |
| `--all` | Flag | Off | Select every discovered profile without a prompt. |
| `--limit` | Positive integer | Unlimited | Submit at most this many eligible URLs, newest first. |
| `--dry-run` | Flag | Off | Build the plan, request storage/cost estimates from Refindery, and submit nothing. |
| `--full` | Flag | Off | Ignore read watermarks; local submission deduplication still applies. |
| `--help` | Flag | — | Show command help. |

No discovered databases is a runtime error with exit status `1`. A missing
`--browser` for `--db`, a missing database file, or a non-positive limit is a
usage error with status `2`. Cancelling the interactive profile picker exits
without submitting.

A non-empty dry run makes a one-shot readiness probe and uses
`POST /v1/pages/estimate/batch` when the server advertises `batch_estimate` and
a token is available. Estimation failures do not fail the command: unresolved
pages use the cached profile for that `server.base_url`, or show unavailable
totals if no profile has been cached yet.

## `list-profiles`

```console
$ refindery-import list-profiles
```

List discovered browser profiles, URL counts, and history paths. Safari access
problems are reported per profile so other profiles can still be listed. If no
profiles are found, the command prints a diagnostic and returns without an
import.

This command does not load or create `config.toml`.

## `status-sweep`

```console
$ refindery-import status-sweep [--config PATH]
```

Re-poll up to 500 recorded nonterminal pages, update local status, and print
status counts. The config defaults to `config.toml`.

## `forget`

```console
$ refindery-import forget TARGET [OPTIONS]
```

Permanently purge a URL or domain from Refindery and create a blacklist rule.
The operation always asks for confirmation.

| Option | Value | Default | Behavior |
| --- | --- | --- | --- |
| `--domain` | Flag | Off | Treat `TARGET` as a complete domain instead of an exact URL. |
| `--reason` | Text | None | Store an explanatory note on the rule. |
| `--config` | Path | `config.toml` | Select the backend configuration. |
| `--help` | Flag | — | Show command help. |

## `blacklist list`

```console
$ refindery-import blacklist list [--config PATH]
```

Print server-side blacklist IDs, kinds, patterns, reasons, and creation times.

## `blacklist remove`

```console
$ refindery-import blacklist remove BLACKLIST_ID [--config PATH]
```

Delete one server-side rule. Future ingestion is allowed again, but previously
purged content remains deleted.

## Common runtime failures

Commands that load a config print a concise error and exit with status `1` for
known runtime failures such as:

- a missing or rejected bearer token;
- Refindery not becoming ready before `server.ready_timeout`;
- Safari Full Disk Access denial; or
- a state database created by a newer, unsupported schema version.

See [troubleshooting](troubleshooting.md) for remediation.
