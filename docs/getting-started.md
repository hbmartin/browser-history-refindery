# Getting started

This guide installs the command, inspects the browser profiles visible on your
Mac, runs a local-only preview, and starts the first import.

## Prerequisites

You need:

- macOS;
- Python 3.13 or newer;
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/);
- a running [Refindery backend](https://github.com/hbmartin/refindery); and
- a bearer token accepted by that backend.

The backend defaults to `http://127.0.0.1:8000`. Deploying or configuring
Refindery itself is outside the scope of this project.

## Install the importer

Install the published command as an isolated tool:

```console
$ uv tool install browser-history-refindery
$ refindery-import --version
```

Upgrade it later with:

```console
$ uv tool upgrade browser-history-refindery
```

## Choose a working directory

By default, the importer creates `config.toml` and
`refindery_state.sqlite3` in the current working directory. Run the command
from one stable, private directory so every invocation finds the same settings
and resume state:

```console
$ mkdir -p ~/.config/refindery-import
$ cd ~/.config/refindery-import
```

Both files contain sensitive operational data. `config.toml` can contain the
bearer token, and the state database records submitted and skipped URLs. Do not
commit or share them.

## Discover browser profiles

List the profiles the importer can read:

```console
$ refindery-import list-profiles
```

The table shows the browser name, browser profile, distinct URL count, and
history database path. See [browser compatibility](reference/browsers.md) for
the discovery rules and known browser families.

### Grant Safari access

macOS protects Safari history with Full Disk Access. If Safari is shown as
`needs Full Disk Access`:

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Enable access for the terminal application that runs the importer.
3. Quit and restart that terminal application.
4. Run `refindery-import list-profiles` again.

You can leave Safari unselected if you do not want to grant access.

## Preview the import

Start an interactive dry run:

```console
$ refindery-import import --dry-run
```

All discovered profiles are initially checked. Adjust the selection, then
confirm it. The report shows how many URLs would be submitted, excluded, or
deduplicated and previews the ten newest eligible URLs.

A dry run does not contact Refindery and does not need a token. It can create
the default config and local state database, and it records the run and its
local exclusion results.

For an unattended preview of every discovered profile:

```console
$ refindery-import import --dry-run --all
```

## Configure Refindery authentication

The first import command creates a commented `config.toml`. Edit its server
section when Refindery is not at the default URL:

```toml
[server]
base_url = "https://refindery.example.com"
auth_token = "replace-with-your-token"
```

For better secret hygiene, leave `auth_token` unset and use an environment
variable instead:

```console
$ export REFINDERY_AUTH_TOKEN="replace-with-your-token"
```

A non-empty `server.auth_token` takes precedence over the environment variable.
See [configuration](reference/configuration.md) for every setting and default.

## Run the first import

Run the command without a subcommand for the default interactive flow:

```console
$ refindery-import
```

Or import every discovered profile without a prompt:

```console
$ refindery-import import --all
```

The importer waits for `GET /readyz` to return `200`, reads and filters the
selected histories, then shows a live dashboard while URLs are submitted and
page statuses are polled.

If pages are still indexing when the import finishes, refresh them later:

```console
$ refindery-import status-sweep
```

Continue with [importing history](guide/importing.md) to understand incremental
runs, limits, retries, and interruption.
