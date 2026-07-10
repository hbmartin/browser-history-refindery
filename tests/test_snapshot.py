"""Snapshot copying and Full Disk Access error mapping."""

import shutil
import sqlite3
from contextlib import closing

import pytest

from browser_history_refindery.browsers import snapshot
from browser_history_refindery.browsers.snapshot import (
    FullDiskAccessError,
    history_snapshot,
    open_readonly,
)


def test_copies_db_and_sidecars(tmp_path):
    db = tmp_path / "History.db"
    db.write_bytes(b"main")
    (tmp_path / "History.db-wal").write_bytes(b"wal")
    (tmp_path / "History.db-shm").write_bytes(b"shm")
    with history_snapshot(db) as snapshot:
        assert snapshot.read_bytes() == b"main"
        assert snapshot.with_name("History.db-wal").read_bytes() == b"wal"
        assert snapshot.with_name("History.db-shm").read_bytes() == b"shm"
        assert snapshot != db
    assert not snapshot.exists()


def test_permission_error_maps_to_fda(tmp_path, monkeypatch):
    db = tmp_path / "History.db"
    db.write_bytes(b"main")

    def deny(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(shutil, "copy2", deny)
    monkeypatch.setattr(snapshot.sys, "platform", "darwin")
    with (
        pytest.raises(FullDiskAccessError, match="Full Disk Access"),
        history_snapshot(db),
    ):
        pass


def test_missing_file_maps_to_fda(tmp_path):
    with pytest.raises(FullDiskAccessError), history_snapshot(tmp_path / "nope.db"):
        pass


def test_open_readonly_encodes_special_characters(tmp_path):
    db = tmp_path / "History #1?.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (value) VALUES ('works')")
        conn.commit()

    with closing(open_readonly(db)) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone() == ("works",)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO sample (value) VALUES ('blocked')")
