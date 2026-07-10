# browser-history-refindery

[![Lint and Test](https://github.com/hbmartin/browser-history-refindery/actions/workflows/lint-test.yml/badge.svg)](https://github.com/hbmartin/browser-history-refindery/actions/workflows/lint-test.yml)
[![Security](https://github.com/hbmartin/browser-history-refindery/actions/workflows/security.yml/badge.svg)](https://github.com/hbmartin/browser-history-refindery/actions/workflows/security.yml)
[![Documentation](https://github.com/hbmartin/browser-history-refindery/actions/workflows/docs.yml/badge.svg)](https://hbmartin.github.io/browser-history-refindery/)
[![PyPI version](https://img.shields.io/pypi/v/browser-history-refindery.svg)](https://pypi.org/project/browser-history-refindery/)
[![Python versions](https://img.shields.io/pypi/pyversions/browser-history-refindery.svg)](https://pypi.org/project/browser-history-refindery/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/hbmartin/browser-history-refindery/blob/main/LICENSE.txt)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Import macOS browser history into a
[Refindery](https://github.com/hbmartin/refindery) backend.

`refindery-import` discovers Safari, Firefox, Chrome, and compatible Chromium
profiles; reads safe snapshot copies of their history databases; applies local
privacy exclusions; and submits eligible URLs through an incremental,
resumable pipeline.

Read the **[complete documentation](https://hbmartin.github.io/browser-history-refindery/)**
for setup, privacy behavior, configuration, command reference, troubleshooting,
and maintainer architecture.

## Quick start

Requires macOS, Python 3.13+, [`uv`](https://docs.astral.sh/uv/), and a running
Refindery backend.

```bash
uv tool install browser-history-refindery
mkdir -p ~/.config/refindery-import
cd ~/.config/refindery-import

refindery-import list-profiles
refindery-import import --dry-run

export REFINDERY_AUTH_TOKEN="replace-with-your-token"
refindery-import
```

The first import command creates a commented `config.toml`. Local progress and
deduplication state are stored in `refindery_state.sqlite3`.

Safari history requires Full Disk Access for the terminal application under
**System Settings → Privacy & Security → Full Disk Access**. Restart the
terminal after granting access.

## Related projects

- [Refindery](https://github.com/hbmartin/refindery) fetches, extracts, and
  indexes submitted pages.
- The [Refindery Chrome extension](https://github.com/hbmartin/refindery-chrome-extension)
  sends individual pages from Chrome and other Chromium browsers.

## Development

See the [maintainer guide](https://hbmartin.github.io/browser-history-refindery/maintainers/development/)
for environment setup, architecture, documentation builds, and the required
quality checks.
