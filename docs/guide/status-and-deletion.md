# Status and deletion

The local state database connects an import request to Refindery's asynchronous
indexing lifecycle. It also supports destructive purge-and-blacklist operations
against the backend.

## Refresh indexing status

The import poller may stop while some pages are still `queued` or `indexing`.
Refresh up to 500 nonterminal page IDs with:

```console
$ refindery-import status-sweep
```

The command polls each page sequentially, updates successful responses, skips
temporary HTTP failures, and prints counts grouped by the latest recorded
status. Run it again if pages remain nonterminal.

Use a non-default config with:

```console
$ refindery-import status-sweep --config /path/to/config.toml
```

The state database selected by that config must be the one used for the
original import.

## Page lifecycle

The importer recognizes these backend page statuses:

- `queued`: accepted but not yet being processed;
- `indexing`: currently being processed;
- `indexed`: terminal success;
- `failed`: a nonterminal failure state that the backend may retry; and
- `dead`: terminal failure, with the last backend error stored when available.

Only `indexed` and `dead` are terminal for polling.

## Purge and blacklist

`forget` permanently purges matching content from Refindery and creates a
server-side blacklist rule so it cannot be ingested again.

Purge one exact URL:

```console
$ refindery-import forget \
    https://example.com/private-page \
    --reason "imported by mistake"
```

Purge every indexed page for a domain:

```console
$ refindery-import forget example.com \
    --domain \
    --reason "private domain"
```

The command shows an explicit confirmation prompt before contacting the
backend. Declining or aborting leaves the backend unchanged. On success it
prints the number of purged pages and the new blacklist ID.

!!! danger "Deletion is not reversible"

    Purged content cannot be restored by removing the blacklist rule. It must be
    ingested and indexed again after the rule is removed.

Use `--config` when the relevant backend is not described by the default
`config.toml`:

```console
$ refindery-import forget example.com \
    --domain \
    --config /path/to/config.toml
```

## Inspect server blacklist rules

List current rules:

```console
$ refindery-import blacklist list
```

The output includes each rule's `bl_...` ID, kind, pattern, optional reason, and
creation time.

## Remove a blacklist rule

Allow future ingests for a target by deleting its rule:

```console
$ refindery-import blacklist remove bl_abc123
```

This operation does not recreate purged pages and does not erase the importer's
local submission history. If the URL must be submitted again, remove or use a
different local state database as appropriate, or enable revisit behavior when
a newer browser visit exists.

## Local state is not a remote deletion log

The state database records ingest and status outcomes, but `forget` and
blacklist administration are server operations and do not rewrite existing
local submission rows. Treat Refindery as the authority for current indexed
content and blacklist rules.
