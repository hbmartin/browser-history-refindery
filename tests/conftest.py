"""Shared fixtures: fixture history databases and a fake home tree."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from browser_history_refindery.browsers.base import (
    BrowserFamily,
    BrowserProfile,
    to_chromium_us,
    to_firefox_us,
    to_safari_s,
)

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def make_chromium_db(
    path: Path, rows: list[tuple[str, str | None, list[datetime], int]]
) -> None:
    """Build a minimal Chromium History DB.

    Each row is (url, title, visit_times, hidden).
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE urls (
            id INTEGER PRIMARY KEY, url TEXT, title TEXT,
            hidden INTEGER DEFAULT 0
        );
        CREATE TABLE visits (
            id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER
        );
        """
    )
    for url_id, (url, title, visits, hidden) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO urls (id, url, title, hidden) VALUES (?, ?, ?, ?)",
            (url_id, url, title, hidden),
        )
        conn.executemany(
            "INSERT INTO visits (url, visit_time) VALUES (?, ?)",
            [(url_id, to_chromium_us(visit)) for visit in visits],
        )
    conn.commit()
    conn.close()


def make_firefox_db(
    path: Path, rows: list[tuple[str, str | None, list[datetime], int]]
) -> None:
    """Build a minimal Firefox places.sqlite."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE moz_places (
            id INTEGER PRIMARY KEY, url TEXT, title TEXT,
            hidden INTEGER DEFAULT 0
        );
        CREATE TABLE moz_historyvisits (
            id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER
        );
        """
    )
    for place_id, (url, title, visits, hidden) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO moz_places (id, url, title, hidden) VALUES (?, ?, ?, ?)",
            (place_id, url, title, hidden),
        )
        conn.executemany(
            "INSERT INTO moz_historyvisits (place_id, visit_date) VALUES (?, ?)",
            [(place_id, to_firefox_us(visit)) for visit in visits],
        )
    conn.commit()
    conn.close()


def make_safari_db(
    path: Path, rows: list[tuple[str, str | None, list[datetime]]]
) -> None:
    """Build a minimal Safari History.db (titles live on visits)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE history_visits (
            id INTEGER PRIMARY KEY, history_item INTEGER,
            visit_time REAL, title TEXT
        );
        """
    )
    for item_id, (url, title, visits) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO history_items (id, url) VALUES (?, ?)", (item_id, url)
        )
        conn.executemany(
            "INSERT INTO history_visits (history_item, visit_time, title)"
            " VALUES (?, ?, ?)",
            [(item_id, to_safari_s(visit), title) for visit in visits],
        )
    conn.commit()
    conn.close()


def profile_for(path: Path, family: BrowserFamily) -> BrowserProfile:
    """A synthetic profile pointing at a fixture database."""
    return BrowserProfile(
        browser_id=f"test-{family}",
        browser_label=f"Test {family}",
        profile_dir="Default",
        profile_name="Test Profile",
        history_path=path,
        family=family,
    )


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """A home directory tree mirroring real browser layouts on this machine."""
    home = tmp_path / "home"
    app_support = home / "Library" / "Application Support"

    chrome = app_support / "Google" / "Chrome"
    for profile_dir in ("Default", "Profile 1"):
        (chrome / profile_dir).mkdir(parents=True)
        make_chromium_db(
            chrome / profile_dir / "History",
            [(f"https://example.com/{profile_dir}", "Example", [T1], 0)],
        )
    (chrome / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Harold"},
                        "Profile 1": {"name": "Work"},
                    }
                }
            }
        )
    )

    dia = app_support / "Dia" / "User Data" / "Default"
    dia.mkdir(parents=True)
    make_chromium_db(dia / "History", [("https://dia.example", "Dia", [T1], 0)])

    firefox = app_support / "Firefox"
    profile_with_places = firefox / "Profiles" / "abc123.default-release"
    profile_with_places.mkdir(parents=True)
    make_firefox_db(
        profile_with_places / "places.sqlite",
        [("https://firefox.example", "Firefox", [T1], 0)],
    )
    (firefox / "Profiles" / "empty.default").mkdir(parents=True)
    (firefox / "profiles.ini").write_text(
        """
[Profile0]
Name=default-release
IsRelative=1
Path=Profiles/abc123.default-release

[Profile1]
Name=default
IsRelative=1
Path=Profiles/empty.default
"""
    )

    safari = home / "Library" / "Safari"
    safari.mkdir(parents=True)
    make_safari_db(safari / "History.db", [("https://safari.example", "Safari", [T1])])

    # An Electron app: has Local State but no info_cache and no History.
    electron = app_support / "SomeElectronApp"
    electron.mkdir(parents=True)
    (electron / "Local State").write_text(json.dumps({"os_crypt": {}}))

    return home
