"""Configuration models and project-local TOML loading."""

import os
import re
import tomllib
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

REFINDERY_AUTH_TOKEN_ENV = "REFINDERY_AUTH_TOKEN"  # noqa: S105 - env var name, not a secret

DEFAULT_CONFIG_PATH = Path("config.toml")

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_LOG_LEVELS = frozenset(
    {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
)
# A permissive hostname label check: letters/digits with internal hyphens, joined
# by single dots. The optional leading ``*.`` wildcard is stripped before checking.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


class MissingTokenError(RuntimeError):
    """Raised when no auth token is configured anywhere."""

    def __init__(self) -> None:
        super().__init__(
            "no auth token configured: set server.auth_token in config.toml or "
            f"export {REFINDERY_AUTH_TOKEN_ENV}"
        )


class ConfigError(RuntimeError):
    """A config file failed validation. Carries a human-readable summary."""

    def __init__(self, path: Path, error: ValidationError) -> None:
        problems = "\n".join(
            f"  - {'.'.join(str(part) for part in err['loc']) or '(root)'}: "
            f"{err['msg']}"
            for err in error.errors()
        )
        super().__init__(f"invalid configuration in {path}:\n{problems}")
        self.path = path


class ServerConfig(BaseModel):
    """Connection settings for the Refindery backend."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://127.0.0.1:8000"
    auth_token: str | None = None
    request_timeout: float = Field(default=30.0, gt=0.0)
    ready_timeout: float = Field(default=60.0, gt=0.0)

    @field_validator("base_url")
    @classmethod
    def _valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in _ALLOWED_URL_SCHEMES or not parts.netloc:
            msg = (
                f"must be an http(s) URL with a host, e.g. http://127.0.0.1:8000 "
                f"(got {value!r})"
            )
            raise ValueError(msg)
        return value.rstrip("/")

    def resolve_token(self) -> str:
        """Return the bearer token from config or environment."""
        if token := self.auth_token or os.environ.get(REFINDERY_AUTH_TOKEN_ENV):
            return token
        raise MissingTokenError


class PacingConfig(BaseModel):
    """Adaptive cool-off tuning for the submitter."""

    model_config = ConfigDict(extra="forbid")

    base_interval: float = Field(default=1.0, gt=0.0)
    floor: float = Field(default=0.5, gt=0.0)
    ceiling: float = Field(default=60.0, gt=0.0)
    backoff_factor: float = Field(default=2.0, ge=1.0)
    recovery_factor: float = Field(default=0.9, gt=0.0, le=1.0)
    max_attempts: int = Field(default=5, ge=1)
    queue_poll_interval: float = Field(default=15.0, gt=0.0)
    queue_depth_threshold: int = Field(default=100, ge=0)
    queue_slowdown_factor: float = Field(default=2.0, ge=1.0)

    @model_validator(mode="after")
    def _coherent_bounds(self) -> Self:
        if self.floor > self.ceiling:
            msg = f"floor ({self.floor}) must not exceed ceiling ({self.ceiling})"
            raise ValueError(msg)
        if self.base_interval > self.ceiling:
            msg = (
                f"base_interval ({self.base_interval}) must not exceed "
                f"ceiling ({self.ceiling})"
            )
            raise ValueError(msg)
        return self


class SubmitConfig(BaseModel):
    """Batch-ingest tuning for the submitter (Refindery >= 0.2.0)."""

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=50, ge=1, le=100)


class PollerConfig(BaseModel):
    """Background page-status poller tuning."""

    model_config = ConfigDict(extra="forbid")

    interval: float = Field(default=5.0, gt=0.0)
    batch_size: int = Field(default=100, ge=1, le=500)
    drain_grace: float = Field(default=60.0, ge=0.0)


class ExclusionsConfig(BaseModel):
    """Which URLs are never submitted."""

    model_config = ConfigDict(extra="forbid")

    banking: bool = True
    health: bool = True
    auth_webmail: bool = True
    adult: bool = False
    skip_domains: list[str] = Field(default_factory=list)
    skip_patterns: list[str] = Field(default_factory=list)

    @field_validator("skip_domains")
    @classmethod
    def _valid_domains(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            domain = raw.strip().lower()
            host = domain.removeprefix("*.")
            malformed = "/" in domain or not _HOSTNAME_RE.match(host)
            if not host or malformed:
                msg = (
                    f"{raw!r} is not a bare domain; use e.g. 'example.com' or "
                    "'*.internal.corp' (no scheme, path, or spaces)"
                )
                raise ValueError(msg)
            cleaned.append(domain)
        return cleaned

    @field_validator("skip_patterns")
    @classmethod
    def _nonempty_patterns(cls, values: list[str]) -> list[str]:
        for pattern in values:
            if not pattern.strip():
                msg = "skip_patterns entries must be non-empty glob patterns"
                raise ValueError(msg)
        return values


class StateConfig(BaseModel):
    """Local progress-tracking database settings."""

    model_config = ConfigDict(extra="forbid")

    db_path: Path = Path("refindery_state.sqlite3")

    @field_validator("db_path")
    @classmethod
    def _nonempty_path(cls, value: Path) -> Path:
        if not str(value).strip():
            msg = "db_path must not be empty"
            raise ValueError(msg)
        return value


class LoggingConfig(BaseModel):
    """Event-log output settings (loguru)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    path: Path = Path("refindery-import.log")
    level: str = "INFO"
    rotation: str = "10 MB"
    retention: str = "10 days"

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            msg = f"level must be one of {sorted(_LOG_LEVELS)} (got {value!r})"
            raise ValueError(msg)
        return level


class ImportConfig(BaseModel):
    """Import behavior toggles."""

    model_config = ConfigDict(extra="forbid")

    resubmit_revisits: bool = False


class AppConfig(BaseModel):
    """Root configuration tree, loaded from a project-local TOML file."""

    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    submit: SubmitConfig = Field(default_factory=SubmitConfig)
    pacing: PacingConfig = Field(default_factory=PacingConfig)
    poller: PollerConfig = Field(default_factory=PollerConfig)
    exclusions: ExclusionsConfig = Field(default_factory=ExclusionsConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    import_: ImportConfig = Field(default_factory=ImportConfig, alias="import")

    @classmethod
    def load(cls, path: Path) -> Self:
        """Parse and validate a TOML config file.

        Raises :class:`pydantic.ValidationError` on any invalid value; callers
        that want a friendly message should use :func:`load_or_create`.
        """
        return cls.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


DEFAULT_CONFIG_TOML = """\
# refindery-import configuration.
# Every key shown here is the built-in default; uncomment to change it.

[server]
base_url = "http://127.0.0.1:8000"
# Bearer token for the Refindery API. If unset, the REFINDERY_AUTH_TOKEN
# environment variable is used instead.
# auth_token = ""
# request_timeout = 30.0
# ready_timeout = 60.0

[submit]
# Batch ingest (POST /v1/pages/batch). Requires Refindery >= 0.2.0.
# batch_size = 50             # URLs per request (1-100)

[pacing]
# Adaptive cool-off between submission batches, in seconds.
# base_interval = 1.0
# floor = 0.5
# ceiling = 60.0
# backoff_factor = 2.0        # applied on timeouts / 5xx / connection errors
# recovery_factor = 0.9       # applied on each success
# max_attempts = 5            # per-URL retries before an error (whole batch retried)
# queue_poll_interval = 15.0  # how often to check the server's pending-job backlog
# queue_depth_threshold = 100 # slow down when the backlog exceeds this
# queue_slowdown_factor = 2.0

[poller]
# Background page-status polling (POST /v1/pages/status/batch).
# interval = 5.0
# batch_size = 100            # page IDs per status request (1-500)
# drain_grace = 60.0          # seconds to keep polling after submission finishes

[exclusions]
# Sensitive-category blocklists (each expands to domains + URL keywords).
banking = true
health = true
auth_webmail = true
adult = false
# Extra skip rules of your own:
# skip_domains = ["example.com", "*.internal.corp"]
# skip_patterns = ["https://*.slack.com/archives/*"]

[state]
# Local progress-tracking database.
# db_path = "refindery_state.sqlite3"

[logging]
# Event log written with loguru. Set enabled = false to turn it off.
# enabled = true
# path = "refindery-import.log"
# level = "INFO"              # TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL
# rotation = "10 MB"
# retention = "10 days"

[import]
# Re-submit a URL when a later run sees visits newer than its last submission
# (bumps the server's revisit count at the cost of extra requests).
# resubmit_revisits = false
"""


def load_or_create(path: Path) -> tuple[AppConfig, bool]:
    """Load the config file, writing a commented default template on first run.

    Returns the config and whether the file was just created. Invalid config
    files raise :class:`ConfigError` with a readable summary of every problem.
    """
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        return AppConfig(), True
    try:
        return AppConfig.load(path), False
    except ValidationError as exc:
        raise ConfigError(path, exc) from exc
