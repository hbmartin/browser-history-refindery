"""Discover installed browsers and their history-bearing profiles."""

import configparser
import json
from dataclasses import dataclass
from pathlib import Path

from browser_history_refindery.browsers.base import BrowserFamily, BrowserProfile

_SQLITE_MAGIC = b"SQLite format 3\x00"


@dataclass(frozen=True, slots=True)
class ChromiumBrowserSpec:
    """A known Chromium-family browser and its data directory."""

    browser_id: str
    label: str
    data_dir: str  # relative to ~/Library/Application Support


KNOWN_CHROMIUM: tuple[ChromiumBrowserSpec, ...] = (
    ChromiumBrowserSpec(
        browser_id="chrome", label="Google Chrome", data_dir="Google/Chrome"
    ),
    ChromiumBrowserSpec(browser_id="comet", label="Comet", data_dir="Comet"),
    ChromiumBrowserSpec(browser_id="dia", label="Dia", data_dir="Dia/User Data"),
    ChromiumBrowserSpec(browser_id="arc", label="Arc", data_dir="Arc/User Data"),
    ChromiumBrowserSpec(
        browser_id="brave", label="Brave", data_dir="BraveSoftware/Brave-Browser"
    ),
    ChromiumBrowserSpec(
        browser_id="edge", label="Microsoft Edge", data_dir="Microsoft Edge"
    ),
    ChromiumBrowserSpec(browser_id="chromium", label="Chromium", data_dir="Chromium"),
    ChromiumBrowserSpec(browser_id="vivaldi", label="Vivaldi", data_dir="Vivaldi"),
    ChromiumBrowserSpec(
        browser_id="opera", label="Opera", data_dir="com.operasoftware.Opera"
    ),
)


def _is_sqlite(path: Path) -> bool:
    """Cheaply check the SQLite magic header without opening (or locking) the DB."""
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def _chromium_profile_names(local_state: Path) -> dict[str, str]:
    """Map profile directory names to display names from a ``Local State`` file."""
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    info_cache = data.get("profile", {}).get("info_cache", {})
    if not isinstance(info_cache, dict):
        return {}
    return {
        profile_dir: str(info.get("name") or profile_dir)
        for profile_dir, info in info_cache.items()
        if isinstance(info, dict)
    }


def _chromium_profiles(
    base_dir: Path, *, browser_id: str, label: str
) -> list[BrowserProfile]:
    """List profiles under one Chromium-style user-data directory."""
    names = _chromium_profile_names(base_dir / "Local State")
    profiles = []
    for child in sorted(base_dir.iterdir()):
        history = child / "History"
        if child.is_dir() and history.is_file() and _is_sqlite(history):
            profiles.append(
                BrowserProfile(
                    browser_id=browser_id,
                    browser_label=label,
                    profile_dir=child.name,
                    profile_name=names.get(child.name, child.name),
                    history_path=history,
                    family=BrowserFamily.CHROMIUM,
                )
            )
    return profiles


def discover_chromium(app_support: Path) -> list[BrowserProfile]:
    """Discover profiles for known Chromium forks plus any unknown ones.

    Unknown forks are detected by the pair of signals a real browser leaves
    behind: a ``Local State`` with ``profile.info_cache`` plus at least one
    profile directory holding a SQLite ``History`` file. Electron apps ship
    ``Local State`` but fail the second condition.
    """
    profiles: list[BrowserProfile] = []
    claimed: set[Path] = set()
    for spec in KNOWN_CHROMIUM:
        base_dir = app_support / spec.data_dir
        if base_dir.is_dir():
            claimed.add(base_dir)
            profiles.extend(
                _chromium_profiles(
                    base_dir, browser_id=spec.browser_id, label=spec.label
                )
            )
    claimed.update((app_support / spec.data_dir).parent for spec in KNOWN_CHROMIUM)
    for vendor_dir in sorted(app_support.iterdir()):
        if not vendor_dir.is_dir() or vendor_dir in claimed:
            continue
        for base_dir in (vendor_dir, vendor_dir / "User Data"):
            if base_dir in claimed or not (base_dir / "Local State").is_file():
                continue
            if not _chromium_profile_names(base_dir / "Local State"):
                continue
            fork_id = vendor_dir.name.lower().replace(" ", "-")
            profiles.extend(
                _chromium_profiles(base_dir, browser_id=fork_id, label=vendor_dir.name)
            )
    return profiles


def discover_firefox(app_support: Path) -> list[BrowserProfile]:
    """Discover Firefox profiles that actually have a ``places.sqlite``."""
    firefox_dir = app_support / "Firefox"
    profiles_ini = firefox_dir / "profiles.ini"
    if not profiles_ini.is_file():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(profiles_ini, encoding="utf-8")
    except configparser.Error:
        return []
    profiles = []
    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        raw_path = parser.get(section, "Path", fallback=None)
        if raw_path is None:
            continue
        is_relative = parser.getboolean(section, "IsRelative", fallback=True)
        profile_path = firefox_dir / raw_path if is_relative else Path(raw_path)
        places = profile_path / "places.sqlite"
        if not places.is_file():
            continue
        profiles.append(
            BrowserProfile(
                browser_id="firefox",
                browser_label="Firefox",
                profile_dir=profile_path.name,
                profile_name=parser.get(section, "Name", fallback=profile_path.name),
                history_path=places,
                family=BrowserFamily.FIREFOX,
            )
        )
    return profiles


def discover_safari(home: Path) -> list[BrowserProfile]:
    """Discover Safari's single history database.

    The directory check succeeds even without Full Disk Access; permission
    problems surface later as ``FullDiskAccessError`` when the file is read.
    """
    safari_dir = home / "Library" / "Safari"
    if not safari_dir.is_dir():
        return []
    return [
        BrowserProfile(
            browser_id="safari",
            browser_label="Safari",
            profile_dir="Safari",
            profile_name="Safari",
            history_path=safari_dir / "History.db",
            family=BrowserFamily.SAFARI,
        )
    ]


def discover_all(home: Path | None = None) -> list[BrowserProfile]:
    """Discover every browser profile with history on this machine."""
    root = home if home is not None else Path.home()
    app_support = root / "Library" / "Application Support"
    profiles = [*discover_safari(root)]
    if app_support.is_dir():
        profiles.extend(discover_chromium(app_support))
        profiles.extend(discover_firefox(app_support))
    return sorted(profiles, key=lambda p: (p.browser_label, p.profile_name))
