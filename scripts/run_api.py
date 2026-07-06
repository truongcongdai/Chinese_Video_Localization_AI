# scripts/run_api.py
"""
Run admin API server.
Usage: python scripts/run_api.py --db ./database.sqlite3 --token your_secret_token
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.universal_video_ai.database import DatabaseManager
from src.universal_video_ai.monitoring import MetricsCollector
from src.universal_video_ai.api import create_app


def main():
    parser = argparse.ArgumentParser(description="Run admin API")
    parser.add_argument("--db", type=Path, required=True, help="Database path")
    parser.add_argument("--token", type=str, required=True, help="Admin token")
    parser.add_argument("--port", type=int, default=5000, help="Port")
    args = parser.parse_args()

    db_manager = DatabaseManager(db_path=args.db)
    db_manager.init_schema()

    metrics = MetricsCollector()

    app = create_app(db_manager, metrics, admin_token=args.token)

    print(f"Admin API running on http://127.0.0.1:{args.port}")
    print(f"Auth token: {args.token}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()