"""Configuration models and project-local TOML loading."""

import os
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

REFINDERY_AUTH_TOKEN_ENV = "REFINDERY_AUTH_TOKEN"  # noqa: S105 - env var name, not a secret

DEFAULT_CONFIG_PATH = Path("config.toml")


class MissingTokenError(RuntimeError):
    """Raised when no auth token is configured anywhere."""

    def __init__(self) -> None:
        super().__init__(
            "no auth token configured: set server.auth_token in config.toml or "
            f"export {REFINDERY_AUTH_TOKEN_ENV}"
        )


class ServerConfig(BaseModel):
    """Connection settings for the Refindery backend."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://127.0.0.1:8000"
    auth_token: str | None = None
    request_timeout: float = 30.0
    ready_timeout: float = 60.0

    def resolve_token(self) -> str:
        """Return the bearer token from config or environment."""
        if token := self.auth_token or os.environ.get(REFINDERY_AUTH_TOKEN_ENV):
            return token
        raise MissingTokenError


class PacingConfig(BaseModel):
    """Adaptive cool-off tuning for the submitter."""

    model_config = ConfigDict(extra="forbid")

    base_interval: float = 1.0
    floor: float = 0.5
    ceiling: float = 60.0
    backoff_factor: float = 2.0
    recovery_factor: float = 0.9
    max_attempts: int = 5
    queue_poll_interval: float = 15.0
    queue_depth_threshold: int = 100
    queue_slowdown_factor: float = 2.0


class PollerConfig(BaseModel):
    """Background page-status poller tuning."""

    model_config = ConfigDict(extra="forbid")

    interval: float = 5.0
    batch_size: int = 20
    drain_grace: float = 60.0


class ExclusionsConfig(BaseModel):
    """Which URLs are never submitted."""

    model_config = ConfigDict(extra="forbid")

    banking: bool = True
    health: bool = True
    auth_webmail: bool = True
    adult: bool = False
    skip_domains: list[str] = Field(default_factory=list)
    skip_patterns: list[str] = Field(default_factory=list)


class StateConfig(BaseModel):
    """Local progress-tracking database settings."""

    model_config = ConfigDict(extra="forbid")

    db_path: Path = Path("refindery_state.sqlite3")


class ImportConfig(BaseModel):
    """Import behavior toggles."""

    model_config = ConfigDict(extra="forbid")

    resubmit_revisits: bool = False


class AppConfig(BaseModel):
    """Root configuration tree, loaded from a project-local TOML file."""

    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    pacing: PacingConfig = Field(default_factory=PacingConfig)
    poller: PollerConfig = Field(default_factory=PollerConfig)
    exclusions: ExclusionsConfig = Field(default_factory=ExclusionsConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    import_: ImportConfig = Field(default_factory=ImportConfig, alias="import")

    @classmethod
    def load(cls, path: Path) -> Self:
        """Parse and validate a TOML config file."""
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

[pacing]
# Adaptive cool-off between URL submissions, in seconds.
# base_interval = 1.0
# floor = 0.5
# ceiling = 60.0
# backoff_factor = 2.0        # applied on timeouts / 5xx / connection errors
# recovery_factor = 0.9       # applied on each success
# max_attempts = 5            # per-URL retries before counting an error
# queue_poll_interval = 15.0  # how often to check the server's pending-job backlog
# queue_depth_threshold = 100 # slow down when the backlog exceeds this
# queue_slowdown_factor = 2.0

[poller]
# Background page-status polling.
# interval = 5.0
# batch_size = 20
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

[import]
# Re-submit a URL when a later run sees visits newer than its last submission
# (bumps the server's revisit count at the cost of extra requests).
# resubmit_revisits = false
"""


def load_or_create(path: Path) -> tuple[AppConfig, bool]:
    """Load the config file, writing a commented default template on first run.

    Returns the config and whether the file was just created.
    """
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        return AppConfig(), True
    return AppConfig.load(path), False
