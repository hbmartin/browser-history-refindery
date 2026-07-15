# Privacy and exclusions

Browser history can reveal private interests, accounts, health concerns, and
internal services. The importer applies local exclusions before any URL is sent
and records why each local exclusion matched.

## What stays on the Mac

The importer never uploads:

- the Safari, Firefox, or Chromium SQLite database;
- a database WAL or shared-memory sidecar;
- cookies, saved passwords, form data, or browser session state; or
- downloaded page HTML or extracted page content.

To avoid reading a live browser database in place, the database and any
`-wal`/`-shm` files are copied into a temporary directory. The copy is opened
read-only and removed when the reader finishes.

The local state database stores URLs, Refindery page IDs, outcomes, errors,
timestamps, status, profile watermarks, exclusion reasons, and a non-secret
fallback estimation profile for each backend. Protect it as sensitive browsing
data.

## What Refindery receives

For an eligible URL, `POST /v1/pages/batch` and the dry-run-only
`POST /v1/pages/estimate/batch` contain:

- `url`;
- the browser title, when available;
- a source such as `history-import:chrome`;
- `fetched_at`, set to the most recent browser visit;
- the local machine hostname;
- the primary browser and profile;
- combined visit count plus first and last visit timestamps; and
- a per-profile source list when the URL appeared in more than one profile.

The bearer token is sent in the HTTP `Authorization` header. During a real
import, Refindery fetches, extracts, persists, and indexes the page. During a
dry run, Refindery may fetch, extract, and chunk it to calculate the estimate,
but the API contract forbids persisting the page or invoking paid providers.

!!! warning "History is metadata"

    Even without page bodies, URLs, titles, profile names, timestamps, and the
    machine hostname can be sensitive. Local exclusions run before both ingest
    and estimation, but a dry run is not offline: review the exclusion settings
    before running it.

## Always-on local exclusions

The following URLs never reach Refindery:

- schemes other than `http` and `https`;
- `localhost` and subdomains of `.localhost`;
- `.local` hostnames; and
- loopback, private, and link-local IP addresses, including RFC 1918 ranges.

These checks cannot be disabled through configuration.

## Sensitive categories

Four built-in categories match known domains and, for some categories, URL path
keywords:

| Category | Default | Examples of coverage |
| --- | --- | --- |
| `banking` | Enabled | Banks, brokerages, payment providers, and cryptocurrency exchanges. |
| `health` | Enabled | Insurers, pharmacies, care providers, and `/mychart` paths. |
| `auth_webmail` | Enabled | Login, OAuth, SSO, password/MFA paths, and major webmail hosts. |
| `adult` | Disabled | A maintained set of adult-content domains. |

Configure them explicitly in `config.toml`:

```toml
[exclusions]
banking = true
health = true
auth_webmail = true
adult = false
```

Category domain matching includes subdomains. Path keywords are case-insensitive
literal substrings of the URL path. Category lists reduce accidental exposure;
they are not exhaustive classifiers.

## Custom domain rules

Add domains that should never be submitted:

```toml
[exclusions]
skip_domains = [
    "example.com",
    "*.internal.corp",
]
```

`example.com` matches both the exact host and any subdomain. A leading `*.` is
accepted but normalized away, so the two forms have the same behavior.

## Custom URL patterns

Use shell-style wildcard patterns against the complete URL:

```toml
[exclusions]
skip_patterns = [
    "https://*.slack.com/archives/*",
    "https://example.net/private/*",
]
```

Patterns use Python `fnmatch` semantics. Matching is case-sensitive against the
full URL string.

## Evaluation order

Only the first match is recorded. Rules run in this order:

1. URL scheme and parseability;
2. local/private host detection;
3. enabled sensitive categories;
4. custom domains; and
5. custom URL patterns.

Dry-run and end-of-run reports group skips by kind. Exact URLs, rule names, and
timestamps are available in the local `skips` table.

## Local exclusions versus server blacklist

Local exclusions prevent a request from leaving the Mac. Refindery's blacklist
is enforced by the backend and can protect every ingest client. A server
blacklist match returns `403` and is recorded locally as a handled submission.

Use [forget and blacklist commands](status-and-deletion.md#purge-and-blacklist)
when content has already reached Refindery. Removing a server blacklist rule
allows future ingestion; it does not restore content that was purged.
