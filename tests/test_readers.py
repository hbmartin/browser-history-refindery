"""Reader tests over minimal fixture databases."""

import sqlite3
from contextlib import closing

from browser_history_refindery.browsers import BrowserFamily, read_profile
from tests.conftest import (
    T0,
    T1,
    T2,
    make_chromium_db,
    make_firefox_db,
    make_safari_db,
    profile_for,
)


def test_chromium_aggregates_and_hides(tmp_path):
    db = tmp_path / "History"
    make_chromium_db(
        db,
        [
            ("https://a.example/", "A", [T0, T2], 0),
            ("https://hidden.example/", "H", [T1], 1),
        ],
    )
    records = read_profile(profile_for(db, BrowserFamily.CHROMIUM))
    assert len(records) == 1
    record = records[0]
    assert record.url == "https://a.example/"
    assert record.visit_count == 2
    assert record.first_visit_at == T0
    assert record.last_visit_at == T2


def test_chromium_since_filters(tmp_path):
    db = tmp_path / "History"
    make_chromium_db(
        db,
        [
            ("https://old.example/", "Old", [T0], 0),
            ("https://new.example/", "New", [T2], 0),
        ],
    )
    profile = profile_for(db, BrowserFamily.CHROMIUM)
    records = read_profile(profile, since=T1)
    assert [record.url for record in records] == ["https://new.example/"]


def test_firefox_reader(tmp_path):
    db = tmp_path / "places.sqlite"
    make_firefox_db(db, [("https://ff.example/", "FF", [T0, T1], 0)])
    records = read_profile(profile_for(db, BrowserFamily.FIREFOX))
    assert len(records) == 1
    assert records[0].visit_count == 2
    assert records[0].last_visit_at == T1


def test_safari_reader_title_from_newest_visit(tmp_path):
    db = tmp_path / "History.db"
    make_safari_db(db, [("https://s.example/", "Initial Title", [T0, T2])])
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE history_visits SET title = 'Old Title' WHERE id = 1")
        conn.execute("UPDATE history_visits SET title = 'Newest Title' WHERE id = 2")
        conn.commit()
    records = read_profile(profile_for(db, BrowserFamily.SAFARI))
    assert len(records) == 1
    assert records[0].title == "Newest Title"
    assert records[0].first_visit_at == T0
    assert records[0].last_visit_at == T2


def test_safari_reader_uses_older_title_after_watermark(tmp_path):
    db = tmp_path / "History.db"
    make_safari_db(db, [("https://s.example/", "Older Title", [T0, T2])])
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE history_visits SET title = NULL WHERE id = 2")
        conn.commit()
    records = read_profile(profile_for(db, BrowserFamily.SAFARI), since=T1)
    assert len(records) == 1
    assert records[0].title == "Older Title"
    assert records[0].last_visit_at == T2
