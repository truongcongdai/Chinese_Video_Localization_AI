"""Owner-scoped execution slots, shared by threads and local worker processes.

Locks are held for the entire synchronous download. The kernel releases them
on exit/crash; lock files are harmless markers and must never be unlinked.
This intentionally uses the same owner scope as autonomous cycle/stage limits.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
import errno
import json
import os
from pathlib import Path
import sqlite3
import time
from concurrent.futures import CancelledError

_scope = ContextVar("autonomous_download_scope", default=None)
MAX_SLOTS = 20


@contextmanager
def download_scope(db_path, user_id, limit):
    token = _scope.set((Path(db_path), int(user_id), max(1, min(MAX_SLOTS, int(limit)))))
    try:
        yield
    finally:
        _scope.reset(token)


def _try_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise


def _unlock(handle):
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class DownloadSlots:
    def __init__(self, db_path, user_id):
        self.db_path = Path(db_path).resolve()
        self.user_id = int(user_id)
        self.directory = self.db_path.parent / (self.db_path.name + ".download-slots") / str(self.user_id)

    @contextmanager
    def configuration_lock(self):
        handle = None
        while handle is None:
            handle = _try_lock(self.directory / "claim.lock")
            if handle is None:
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(handle)

    def active_count(self):
        """Caller holds configuration_lock, so concurrent claims cannot race."""
        count = 0
        for index in range(MAX_SLOTS):
            handle = _try_lock(self.directory / f"{index}.lock")
            if handle is None:
                count += 1
            else:
                _unlock(handle)
        return count

    def configured_limit(self, fallback=MAX_SLOTS):
        if not self.db_path.exists():
            return fallback
        with closing(sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='autonomous_channel_configs'"
            ).fetchone():
                return fallback
            rows = conn.execute(
                "SELECT configuration_json FROM autonomous_channel_configs WHERE user_id=?",
                (self.user_id,),
            ).fetchall()
        # Pausing a schedule does not remove the cap on its in-flight/retried work.
        return min([fallback] + [
            max(1, min(MAX_SLOTS, int(json.loads(row[0]).get("max_concurrent_downloads", 2))))
            for row in rows
        ])

    @contextmanager
    def acquire(self, *, limit=MAX_SLOTS, cancel_event=None):
        handle = None
        while handle is None:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("Download cancelled while waiting for capacity.")
            with self.configuration_lock():
                cap = self.configured_limit(limit)
                if self.active_count() < cap:
                    for index in range(MAX_SLOTS):
                        handle = _try_lock(self.directory / f"{index}.lock")
                        if handle is not None:
                            break
            if handle is None:
                if cancel_event is not None:
                    cancel_event.wait(0.05)
                else:
                    time.sleep(0.05)
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("Download cancelled before execution.")
            yield
        finally:
            _unlock(handle)


@contextmanager
def download_slot(user_id=None, *, db_path=None, cancel_event=None):
    scope = _scope.get()
    limit = MAX_SLOTS
    if scope is not None:
        scoped_db, scoped_owner, limit = scope
        if user_id is not None and int(user_id) != scoped_owner:
            raise ValueError("Download owner does not match autonomous execution owner.")
        user_id, db_path = scoped_owner, scoped_db
    if user_id is None:
        yield
        return
    if db_path is None:
        from universal_video_ai.config import TEMP_DIR
        db_path = Path(os.environ.get("WEB_DB_PATH", Path(TEMP_DIR) / "database.sqlite3"))
    with DownloadSlots(db_path, user_id).acquire(limit=limit, cancel_event=cancel_event):
        yield
