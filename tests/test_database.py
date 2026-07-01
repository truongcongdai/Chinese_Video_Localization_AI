# tests/test_database.py
from pathlib import Path
import time

import pytest

from universal_video_ai.database import DatabaseManager, DownloadRecord

def test_schema_and_add_get(tmp_path: Path):
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    url = "https://example.com/video"
    platform = "youtube"
    rec_id = manager.add_download(url=url, platform=platform, title="My Video")
    assert isinstance(rec_id, int) and rec_id > 0

    rec = manager.get_download(rec_id)
    assert isinstance(rec, DownloadRecord)
    assert rec.url == url
    assert rec.platform == platform
    assert rec.title == "My Video"
    assert rec.status == "pending"


def test_update_and_list(tmp_path: Path):
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    id1 = manager.add_download(url="u1", platform="p1")
    id2 = manager.add_download(url="u2", platform="p2")

    manager.update_download(id1, status="completed", video_path=str(Path("/tmp/v1.mp4")))
    rec1 = manager.get_download(id1)
    assert rec1.status == "completed"
    assert rec1.video_path == str(Path("/tmp/v1.mp4"))

    recs = manager.list_downloads(limit=10)
    assert len(recs) >= 2


def test_delete(tmp_path: Path):
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    rid = manager.add_download(url="to-delete", platform="p")
    assert manager.get_download(rid) is not None
    manager.delete_download(rid)
    assert manager.get_download(rid) is None


# -------------------------
# New tests for credit system
# -------------------------
def test_get_user_credits_new_user(tmp_path: Path):
    """New user should get 3 free credits."""
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    credits = manager.get_user_credits(user_id=123)
    assert credits is not None
    assert credits.user_id == 123
    assert pytest.approx(credits.credits, 0.001) == 3.0
    assert pytest.approx(credits.total_used, 0.001) == 0.0


def test_deduct_credits_success(tmp_path: Path):
    """Deduct credits when sufficient balance."""
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    credits = manager.get_user_credits(user_id=123)
    assert pytest.approx(credits.credits, 0.001) == 3.0

    result = manager.deduct_credits(user_id=123, amount=1.0)
    assert result is True

    credits = manager.get_user_credits(user_id=123)
    assert pytest.approx(credits.credits, 0.001) == 2.0
    assert pytest.approx(credits.total_used, 0.001) == 1.0


def test_deduct_credits_insufficient(tmp_path: Path):
    """Deduct credits when insufficient balance."""
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    result = manager.deduct_credits(user_id=123, amount=5.0)
    assert result is False

    credits = manager.get_user_credits(user_id=123)
    assert pytest.approx(credits.credits, 0.001) == 3.0
    assert pytest.approx(credits.total_used, 0.001) == 0.0


def test_add_credits(tmp_path: Path):
    """Add credits to user."""
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    # ensure default exists
    manager.get_user_credits(user_id=123)
    manager.add_credits(user_id=123, amount=5.0)

    credits = manager.get_user_credits(user_id=123)
    assert pytest.approx(credits.credits, 0.001) == 8.0  # 3 default + 5 added


def test_set_user_credits(tmp_path: Path):
    """Test set_user_credits admin API sets exact balance."""
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    # Ensure default exists (3.0)
    credits = manager.get_user_credits(user_id=42)
    assert pytest.approx(credits.credits, 0.001) == 3.0

    # Set to 10
    manager.set_user_credits(user_id=42, new_balance=10.0)
    credits2 = manager.get_user_credits(user_id=42)
    assert pytest.approx(credits2.credits, 0.001) == 10.0

    # Set to 0.5
    manager.set_user_credits(user_id=42, new_balance=0.5)
    credits3 = manager.get_user_credits(user_id=42)
    assert pytest.approx(credits3.credits, 0.001) == 0.5