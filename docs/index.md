# Refindery Browser History Importer

Turn the useful pages already hiding in your macOS browser history into a
searchable Refindery library.

`refindery-import` discovers Safari, Firefox, Chrome, and Chromium-based browser
profiles, applies privacy-focused exclusions, and sends eligible URLs to a
[Refindery](https://github.com/hbmartin/refindery) backend. Refindery fetches and
indexes the pages; the importer never uploads browser database files or saved
page bodies.

[Get started](getting-started.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/hbmartin/browser-history-refindery){ .md-button }

## Why use it?

- **One import across every profile.** Browser and profile discovery includes
  Safari, Firefox, known Chromium browsers, and compatible Chromium forks.
- **Safe access to live history.** Each SQLite database and its write-ahead-log
  sidecars are copied to a temporary directory before being read.
- **Incremental and resumable.** A local state database records submissions and
  per-profile watermarks, so later runs focus on new visits.
- **Privacy controls before upload.** Private hosts, unsupported URL schemes,
  sensitive categories, and custom rules are excluded locally.
- **Backlog-aware delivery.** The submitter retries transient failures and
  slows down when Refindery has a large pending-job queue.

## What to read next

- [Getting started](getting-started.md) walks through installation, discovery,
  a dry run, authentication, and the first real import.
- [Importing history](guide/importing.md) explains profile selection, limits,
  full scans, deduplication, interruption, and resumability.
- [Privacy and exclusions](guide/privacy.md) documents what leaves the machine
  and how every local and server-side filter behaves.
- [CLI reference](reference/cli.md) and
  [configuration reference](reference/configuration.md) list every supported
  command, option, setting, and default.
- [Architecture and backend contract](maintainers/architecture.md) is the
  starting point for maintainers and backend integrators.

!!! note "Platform support"

    The importer currently supports macOS and Python 3.13 or newer. It requires
    a separately running Refindery backend.
