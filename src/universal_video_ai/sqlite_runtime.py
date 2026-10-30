"""SQLite transactions with deterministic connection ownership."""
from __future__ import annotations

import sqlite3
from typing import Any

__all__ = ["ClosingConnection"]


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then close; raw callers can still close explicitly."""

    def __exit__(self, *args: Any) -> bool:
        try:
            return super().__exit__(*args)
        finally:
            self.close()
