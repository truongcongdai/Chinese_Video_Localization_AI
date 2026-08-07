
# scripts/db_admin.py
"""
Simple CLI tool for database administration (add/set credits, view user balance).

Usage:
    python scripts/db_admin.py --db /path/to/database.sqlite3 --addcredits 123 5.0
    python scripts/db_admin.py --db /path/to/database.sqlite3 --setcredits 123 10.0
    python scripts/db_admin.py --db /path/to/database.sqlite3 --getcredits 123
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from universal_video_ai.database import DatabaseManager


def main():
    parser = argparse.ArgumentParser(description="Database admin CLI")
    parser.add_argument("--db", type=Path, default=Path.cwd() / "temp" / "database.sqlite3",
                        help="Path to database file (default: temp/database.sqlite3)")
    parser.add_argument("--addcredits", nargs=2, metavar=("USER_ID", "AMOUNT"),
                        help="Add credits to user: --addcredits 123 5.0")
    parser.add_argument("--setcredits", nargs=2, metavar=("USER_ID", "AMOUNT"),
                        help="Set user credits: --setcredits 123 10.0")
    parser.add_argument("--getcredits", type=int, metavar="USER_ID",
                        help="Get user credit balance: --getcredits 123")

    args = parser.parse_args()

    # Initialize database manager
    manager = DatabaseManager(db_path=args.db)
    manager.init_schema()

    if args.addcredits:
        try:
            user_id = int(args.addcredits[0])
            amount = float(args.addcredits[1])
            manager.add_credits(user_id, amount)
            uc = manager.get_user_credits(user_id)
            print(f"✓ Added {amount:.2f} credits to user {user_id}. New balance: {uc.credits:.2f}")
        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.setcredits:
        try:
            user_id = int(args.setcredits[0])
            amount = float(args.setcredits[1])
            manager.set_user_credits(user_id, amount)
            uc = manager.get_user_credits(user_id)
            print(f"✓ Set credits for user {user_id} to {uc.credits:.2f}")
        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.getcredits:
        try:
            uc = manager.get_user_credits(args.getcredits)
            print(f"User {args.getcredits}:")
            print(f"  Balance: {uc.credits:.2f} credits")
            print(f"  Total Used: {uc.total_used:.2f} credits")
            print(f"  Created: {uc.created_at}")
        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()