# Configuration reference

Commands that need configuration load `config.toml` from the current working
directory unless `--config` selects another path. If it does not exist, the
command writes a commented template containing the built-in defaults.

Unknown sections and keys are rejected. Numeric durations are seconds.
Relative paths, including `state.db_path`, are resolved from the process's
working directory rather than the config file's parent directory.

## Complete example

```toml
[server]
base_url = "http://127.0.0.1:8000"
# auth_token = ""
request_timeout = 30.0
ready_timeout = 60.0

[pacing]
base_interval = 1.0
floor = 0.5
ceiling = 60.0
backoff_factor = 2.0
recovery_factor = 0.9
max_attempts = 5
queue_poll_interval = 15.0
queue_depth_threshold = 100
queue_slowdown_factor = 2.0

[poller]
interval = 5.0
batch_size = 20
drain_grace = 60.0

[exclusions]
banking = true
health = true
auth_webmail = true
adult = false
skip_domains = []
skip_patterns = []

[state]
db_path = "refindery_state.sqlite3"

[import]
resubmit_revisits = false
```

## `[server]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `base_url` | String | `http://127.0.0.1:8000` | Refindery origin used for every API request. |
| `auth_token` | String or omitted | Omitted | Bearer token. A non-empty value takes precedence over `REFINDERY_AUTH_TOKEN`. |
| `request_timeout` | Float | `30.0` | Timeout applied to individual HTTP requests. |
| `ready_timeout` | Float | `60.0` | Maximum total wait for `GET /readyz` to report ready. |

### `REFINDERY_AUTH_TOKEN`

When `server.auth_token` is absent or empty, the importer reads
`REFINDERY_AUTH_TOKEN` from the environment:

```console
$ export REFINDERY_AUTH_TOKEN="replace-with-your-token"
```

A real import, status request that contacts the backend, or blacklist operation
fails if neither source contains a token. Dry runs do not resolve a token.

## `[pacing]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `base_interval` | Float | `1.0` | Initial delay between submissions. |
| `floor` | Float | `0.5` | Lowest recovered interval. |
| `ceiling` | Float | `60.0` | Highest backed-off interval. |
| `backoff_factor` | Float | `2.0` | Multiplier after a transport error or retryable server response. |
| `recovery_factor` | Float | `0.9` | Multiplier after success, bounded by `floor`. |
| `max_attempts` | Integer | `5` | Attempts per URL before it is counted as an error. |
| `queue_poll_interval` | Float | `15.0` | Delay between pending-job backlog checks. |
| `queue_depth_threshold` | Integer | `100` | Backlog size above which queue slowdown applies. |
| `queue_slowdown_factor` | Float | `2.0` | Multiplier applied while the backlog is above the threshold. |

The effective interval combines the adaptive interval and queue slowdown and
never exceeds `ceiling`.

## `[poller]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `interval` | Float | `5.0` | Delay between page-status polling rounds. |
| `batch_size` | Integer | `20` | Maximum page IDs loaded for one polling round. |
| `drain_grace` | Float | `60.0` | Time to keep polling after all submissions finish. |

## `[exclusions]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `banking` | Boolean | `true` | Enable the banking and financial-services category. |
| `health` | Boolean | `true` | Enable the healthcare category. |
| `auth_webmail` | Boolean | `true` | Enable authentication, account, and webmail rules. |
| `adult` | Boolean | `false` | Enable the adult-content domain category. |
| `skip_domains` | String array | `[]` | Exact domains and all subdomains to exclude. |
| `skip_patterns` | String array | `[]` | Shell-style wildcard patterns matched against full URLs. |

See [privacy and exclusions](../guide/privacy.md) for matching semantics,
evaluation order, and the data sent for allowed URLs.

## `[state]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `db_path` | Path string | `refindery_state.sqlite3` | Local SQLite database used for runs, submissions, skips, statuses, and watermarks. |

The database uses WAL mode, so `-wal` and `-shm` sidecars may appear while it is
open. Keep all of them private and do not delete or move them during a run.

The importer migrates older supported state schemas in place. It refuses to
open a database with a schema version newer than the running build supports.

## `[import]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `resubmit_revisits` | Boolean | `false` | Re-submit a known URL when browser history contains a visit newer than its previous submission. |

This option can generate additional backend requests. It does not retry URLs
that were permanently rejected with HTTP `422`.

## Multiple environments

Keep separate config and state files for separate Refindery instances:

```toml
# work.toml
[server]
base_url = "https://refindery.work.example"

[state]
db_path = "refindery-work.sqlite3"
```

Then use the same config consistently:

```console
$ refindery-import import --config work.toml --all
$ refindery-import status-sweep --config work.toml
```
