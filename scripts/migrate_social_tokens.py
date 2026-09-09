"""Encrypt legacy social-account tokens in an explicitly selected database."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from universal_video_ai.secret_cipher import SecretCipherError, TokenCipher
from universal_video_ai.web.store import Store


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stop database writers and back up first. Encrypt legacy tokens; never prints secrets."
    )
    parser.add_argument("--db", type=Path, required=True, help="Existing SQLite database path (test a COPY first)")
    parser.add_argument("--user-id", type=int, help="Optional owner-scoped migration")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("--db must name an existing database; no file was created.")
    try:
        cipher = TokenCipher()
        cipher.require_key()
        connection = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            social_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='social_accounts'"
            ).fetchone()
        finally:
            connection.close()
        if "id" not in columns or not social_table:
            parser.error("--db must be an initialized web Store database; unrelated databases are not migrated.")
        store = Store(args.db, token_cipher=cipher)
        migrated = store.migrate_legacy_social_account_tokens(args.user_id)
    except SecretCipherError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Migrated credential rows: {migrated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
