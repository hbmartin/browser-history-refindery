"""Discover installed browsers and their history-bearing profiles.

Discovery is platform-aware: on macOS profiles live under
``~/Library/Application Support`` (plus Safari under ``~/Library/Safari``); on
Linux Chromium forks live under ``~/.config`` and Firefox under ``~/.mozilla``
(or the Snap sandbox). The per-family readers are identical across platforms —
only the search roots differ.
"""

import configparser
import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from browser_history_refindery.browsers.base import BrowserFamily, BrowserProfile

_SQLITE_MAGIC = b"SQLite format 3\x00"


class Platform(StrEnum):
    """Operating systems whose browser layouts we know how to discover."""

    MACOS = "macos"
    LINUX = "linux"


def _detect_platform(name: str) -> Platform | None:
    """Map a ``sys.platform`` string to a supported :class:`Platform`."""
    if name == "darwin":
        return Platform.MACOS
    if name.startswith("linux"):
        return Platform.LINUX
    return None


@dataclass(frozen=True, slots=True)
class ChromiumBrowserSpec:
    """A known Chromium-family browser and its per-platform data directory.

    ``macos_dir`` is relative to ``~/Library/Application Support``; ``linux_dir``
    is relative to ``~/.config`` and is ``None`` for browsers with no Linux build.
    """

    browser_id: str
    label: str
    macos_dir: str
    linux_dir: str | None = None

    def data_dir(self, platform: Platform) -> str | None:
        """Return this browser's data directory on ``platform``, if any."""
        match platform:
            case Platform.MACOS:
                return self.macos_dir
            case Platform.LINUX:
                return self.linux_dir


KNOWN_CHROMIUM: tuple[ChromiumBrowserSpec, ...] = (
    ChromiumBrowserSpec(
        browser_id="chrome",
        label="Google Chrome",
        macos_dir="Google/Chrome",
        linux_dir="google-chrome",
    ),
    ChromiumBrowserSpec(browser_id="comet", label="Comet", macos_dir="Comet"),
    ChromiumBrowserSpec(browser_id="dia", label="Dia", macos_dir="Dia/User Data"),
    ChromiumBrowserSpec(browser_id="arc", label="Arc", macos_dir="Arc/User Data"),
    ChromiumBrowserSpec(
        browser_id="brave",
        label="Brave",
        macos_dir="BraveSoftware/Brave-Browser",
        linux_dir="BraveSoftware/Brave-Browser",
    ),
    ChromiumBrowserSpec(
        browser_id="edge",
        label="Microsoft Edge",
        macos_dir="Microsoft Edge",
        linux_dir="microsoft-edge",
    ),
    ChromiumBrowserSpec(
        browser_id="chromium",
        label="Chromium",
        macos_dir="Chromium",
        linux_dir="chromium",
    ),
    ChromiumBrowserSpec(
        browser_id="vivaldi", label="Vivaldi", macos_dir="Vivaldi", linux_dir="vivaldi"
    ),
    ChromiumBrowserSpec(
        browser_id="opera",
        label="Opera",
        macos_dir="com.operasoftware.Opera",
        linux_dir="opera",
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


def discover_chromium(root: Path, *, platform: Platform) -> list[BrowserProfile]:
    """Discover profiles for known Chromium forks plus any unknown ones.

    ``root`` is ``~/Library/Application Support`` on macOS or ``~/.config`` on
    Linux. Unknown forks are detected by the pair of signals a real browser
    leaves behind: a ``Local State`` with ``profile.info_cache`` plus at least
    one profile directory holding a SQLite ``History`` file. Electron apps ship
    ``Local State`` but fail the second condition.
    """
    profiles: list[BrowserProfile] = []
    claimed: set[Path] = set()
    for spec in KNOWN_CHROMIUM:
        if (rel := spec.data_dir(platform)) is None:
            continue
        base_dir = root / rel
        if base_dir.is_dir():
            claimed.add(base_dir)
            profiles.extend(
                _chromium_profiles(
                    base_dir, browser_id=spec.browser_id, label=spec.label
                )
            )
        claimed.add(base_dir.parent)
    profiles.extend(_discover_unknown_forks(root, claimed=claimed))
    return profiles


def _discover_unknown_forks(root: Path, *, claimed: set[Path]) -> list[BrowserProfile]:
    """Find Chromium forks not in ``KNOWN_CHROMIUM`` under ``root``."""
    profiles: list[BrowserProfile] = []
    for vendor_dir in sorted(root.iterdir()):
        if not vendor_dir.is_dir() or vendor_dir in claimed:
            continue
        for base_dir in (vendor_dir, vendor_dir / "User Data"):
            local_state = base_dir / "Local State"
            if base_dir in claimed or not local_state.is_file():
                continue
            if not _chromium_profile_names(local_state):
                continue
            fork_id = vendor_dir.name.lower().replace(" ", "-")
            profiles.extend(
                _chromium_profiles(base_dir, browser_id=fork_id, label=vendor_dir.name)
            )
    return profiles


def discover_firefox(firefox_dir: Path) -> list[BrowserProfile]:
    """Discover Firefox profiles (with a ``places.sqlite``) under ``firefox_dir``.

    ``firefox_dir`` is the directory that holds ``profiles.ini`` —
    ``~/Library/Application Support/Firefox`` on macOS, ``~/.mozilla/firefox``
    (or the Snap sandbox equivalent) on Linux.
    """
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
    """Discover Safari's single history database (macOS only).

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


def _discover_macos(root: Path) -> list[BrowserProfile]:
    app_support = root / "Library" / "Application Support"
    profiles = [*discover_safari(root)]
    if app_support.is_dir():
        profiles.extend(discover_chromium(app_support, platform=Platform.MACOS))
        profiles.extend(discover_firefox(app_support / "Firefox"))
    return profiles


def _discover_linux(root: Path) -> list[BrowserProfile]:
    profiles: list[BrowserProfile] = []
    if (config_dir := root / ".config").is_dir():
        profiles.extend(discover_chromium(config_dir, platform=Platform.LINUX))
    for firefox_dir in (
        root / ".mozilla" / "firefox",
        root / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
    ):
        profiles.extend(discover_firefox(firefox_dir))
    return profiles


def discover_all(
    home: Path | None = None, *, platform_name: str = sys.platform
) -> list[BrowserProfile]:
    """Discover every browser profile with history on this machine.

    ``platform_name`` defaults to ``sys.platform`` and is injectable for tests.
    Unsupported platforms yield an empty list rather than raising.
    """
    root = home if home is not None else Path.home()
    profiles: list[BrowserProfile] = []
    match _detect_platform(platform_name):
        case Platform.MACOS:
            profiles = _discover_macos(root)
        case Platform.LINUX:
            profiles = _discover_linux(root)
        case None:
            pass
    unique = {profile.history_path: profile for profile in profiles}
    return sorted(unique.values(), key=lambda p: (p.browser_label, p.profile_name))
