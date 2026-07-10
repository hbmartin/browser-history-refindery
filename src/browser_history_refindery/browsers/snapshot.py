"""Copy-then-read access to live browser history databases."""

import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

_SIDECAR_SUFFIXES = ("-wal", "-shm")

_MACOS_HINT = (
    "grant Full Disk Access to your terminal app in System Settings > "
    "Privacy & Security > Full Disk Access, then restart the terminal"
)
_OTHER_HINT = (
    "check that your user can read the file (close the browser if it holds a "
    "lock, or the file may be owned by another user)"
)


class FullDiskAccessError(RuntimeError):
    """Raised when the OS blocks reading a history database (permissions)."""

    def __init__(self, db_path: Path) -> None:
        hint = _MACOS_HINT if sys.platform == "darwin" else _OTHER_HINT
        super().__init__(f"cannot read {db_path}: {hint}")
        self.db_path = db_path


@contextmanager
def history_snapshot(db_path: Path) -> Generator[Path]:
    """Yield a temporary copy of a history database.

    The database and any ``-wal``/``-shm`` sidecars are copied so the live
    database can stay locked by a running browser while we read the copy.
    """
    with tempfile.TemporaryDirectory(prefix="refindery-history-") as tmp:
        target = Path(tmp) / db_path.name
        try:
            shutil.copy2(src=db_path, dst=target)
            for suffix in _SIDECAR_SUFFIXES:
                if (sidecar := db_path.with_name(db_path.name + suffix)).exists():
                    shutil.copy2(
                        src=sidecar, dst=target.with_name(target.name + suffix)
                    )
        except (PermissionError, FileNotFoundError) as exc:
            raise FullDiskAccessError(db_path) from exc
        yield target


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database in read-only mode."""
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
