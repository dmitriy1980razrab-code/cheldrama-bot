from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os


class UpdateAlreadyRunning(RuntimeError):
    pass


def _lock(file) -> None:
    file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file) -> None:
    file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def update_lock(path: str | Path):
    """Allow only one update process without leaving a stale lock after a crash."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    file = lock_path.open("a+b")
    if file.seek(0, 2) == 0:
        file.write(b"0")
        file.flush()
    try:
        try:
            _lock(file)
        except OSError as error:
            raise UpdateAlreadyRunning("database update is already running") from error
        try:
            yield
        finally:
            _unlock(file)
    finally:
        file.close()
