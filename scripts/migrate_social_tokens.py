"""Encrypt legacy social-account tokens in a disposable or backed-up database."""
from __future__ import annotations

import argparse
from pathlib import Path

from universal_video_ai.web.store import Store


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently encrypt legacy plaintext social-account tokens."
    )
    parser.add_argument("--db", type=Path, required=True, help="SQLite database path")
    parser.add_argument("--user-id", type=int, help="Optional owner-scoped migration")
    args = parser.parse_args()
    store = Store(args.db)
    migrated = store.migrate_legacy_social_account_tokens(args.user_id)
    print(f"Migrated social account rows: {migrated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
