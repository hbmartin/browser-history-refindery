# Browser compatibility

The importer currently discovers macOS history databases for Safari, Firefox,
known Chromium browsers, and compatible Chromium forks. Browser readers are
schema-based: a directly supplied database can use any of the three supported
families.

## Support matrix

| Browser or family | Schema family | Typical history location |
| --- | --- | --- |
| Safari | `safari` | `~/Library/Safari/History.db` |
| Firefox | `firefox` | `~/Library/Application Support/Firefox/Profiles/.../places.sqlite` |
| Google Chrome | `chromium` | `~/Library/Application Support/Google/Chrome/<profile>/History` |
| Arc | `chromium` | `~/Library/Application Support/Arc/User Data/<profile>/History` |
| Brave | `chromium` | `~/Library/Application Support/BraveSoftware/Brave-Browser/<profile>/History` |
| Microsoft Edge | `chromium` | `~/Library/Application Support/Microsoft Edge/<profile>/History` |
| Chromium | `chromium` | `~/Library/Application Support/Chromium/<profile>/History` |
| Vivaldi | `chromium` | `~/Library/Application Support/Vivaldi/<profile>/History` |
| Opera | `chromium` | `~/Library/Application Support/com.operasoftware.Opera/<profile>/History` |
| Comet | `chromium` | `~/Library/Application Support/Comet/<profile>/History` |
| Dia | `chromium` | `~/Library/Application Support/Dia/User Data/<profile>/History` |

Exact paths can vary between browser releases and installations. Run
`refindery-import list-profiles` to see the paths found on the current Mac.

## Chromium discovery

Known browser data directories are checked first. A child directory becomes a
profile only when it contains a file named `History` whose header has SQLite's
magic bytes. Display names are read from `profile.info_cache` in the browser's
`Local State` JSON when available.

Unknown Chromium forks can also be discovered beneath
`~/Library/Application Support`. They must provide both:

- a `Local State` file with non-empty `profile.info_cache`; and
- at least one profile directory with a SQLite `History` file.

The pair of signals avoids treating unrelated Electron applications as
browsers.

## Firefox discovery

The importer reads `Firefox/profiles.ini`, resolves relative and absolute
profile paths, and includes sections whose profile directory contains
`places.sqlite`. The configured Firefox profile name is shown when available.

## Safari discovery and permissions

Safari exposes one profile at `~/Library/Safari/History.db`. Merely discovering
the Safari directory does not prove the database is readable. Snapshot copying
can still be denied by macOS privacy controls.

Grant Full Disk Access to the terminal application under **System Settings →
Privacy & Security → Full Disk Access**, then restart the terminal. The importer
maps the permission failure to a focused remediation message.

## Safe database access

Readers never open the live database directly. The main database plus existing
`-wal` and `-shm` sidecars are copied into a temporary directory, and the copy
is opened with SQLite read-only mode. This avoids browser locks and incorporates
recent visits that have not yet been checkpointed from the WAL.

The copy is a point-in-time best effort. A browser writing during the separate
file copies can still produce an inconsistent snapshot; retry after the browser
quiets or exits if SQLite reports corruption.

## Visit aggregation

Every reader returns one record per URL per profile with:

- a title when the browser has one;
- visit count;
- earliest visit in the selected read window; and
- latest visit in the selected read window.

Browser-specific timestamp epochs are converted to timezone-aware UTC values.
Safari can reuse an older non-empty title when the newest visit is untitled.

## Read one database directly

Use `--db` and the matching family when discovery does not cover a compatible
browser installation:

```console
$ refindery-import import \
    --db "/custom/profile/History" \
    --browser chromium
```

Direct mode labels the source as a custom profile and otherwise follows the
same snapshot, filtering, submission, and state rules.
