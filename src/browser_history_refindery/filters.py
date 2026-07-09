"""Client-side URL exclusion rules applied before any submission."""

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from urllib.parse import SplitResult, urlsplit

from browser_history_refindery.config import ExclusionsConfig
from browser_history_refindery.filter_categories import ALL_CATEGORIES, Category

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class SkipKind(StrEnum):
    """Why a URL was skipped, as recorded in the local state database."""

    SCHEME = "scheme"
    PRIVATE_HOST = "private-host"
    CATEGORY = "category"
    USER_PATTERN = "user-pattern"


@dataclass(frozen=True, slots=True)
class SkipReason:
    """The exclusion rule a URL matched."""

    kind: SkipKind
    rule: str


def _domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _is_private_host(host: str) -> str | None:
    """Return the matching rule name when the host is local or private."""
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.is_loopback or address.is_private or address.is_link_local:
        return host
    return None


class ExclusionEngine:
    """Evaluates every exclusion rule against a URL, in a fixed order."""

    def __init__(self, config: ExclusionsConfig) -> None:
        enabled = {
            "banking": config.banking,
            "health": config.health,
            "auth_webmail": config.auth_webmail,
            "adult": config.adult,
        }
        self._categories: tuple[Category, ...] = tuple(
            category for category in ALL_CATEGORIES if enabled[category.name]
        )
        self._skip_domains: tuple[str, ...] = tuple(
            domain.removeprefix("*.") for domain in config.skip_domains
        )
        self._skip_patterns: tuple[str, ...] = tuple(config.skip_patterns)

    def check(self, url: str) -> SkipReason | None:
        """Return the first matching skip rule, or None when the URL is allowed."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return SkipReason(kind=SkipKind.SCHEME, rule="unparseable")
        if parts.scheme not in _ALLOWED_SCHEMES:
            return SkipReason(kind=SkipKind.SCHEME, rule=parts.scheme or "empty")
        host = (parts.hostname or "").lower()
        if private_rule := _is_private_host(host):
            return SkipReason(kind=SkipKind.PRIVATE_HOST, rule=private_rule)
        if category_reason := self._check_categories(host, parts):
            return category_reason
        return self._check_user_rules(host, url)

    def _check_categories(self, host: str, parts: SplitResult) -> SkipReason | None:
        path = parts.path.lower()
        for category in self._categories:
            for domain in category.domains:
                if _domain_matches(host, domain):
                    return SkipReason(
                        kind=SkipKind.CATEGORY,
                        rule=f"category:{category.name} domain={domain}",
                    )
            for keyword in category.path_keywords:
                if keyword in path:
                    return SkipReason(
                        kind=SkipKind.CATEGORY,
                        rule=f"category:{category.name} path={keyword}",
                    )
        return None

    def _check_user_rules(self, host: str, url: str) -> SkipReason | None:
        for domain in self._skip_domains:
            if _domain_matches(host, domain):
                return SkipReason(kind=SkipKind.USER_PATTERN, rule=f"domain={domain}")
        for pattern in self._skip_patterns:
            if fnmatch(url, pattern):
                return SkipReason(kind=SkipKind.USER_PATTERN, rule=f"pattern={pattern}")
        return None
