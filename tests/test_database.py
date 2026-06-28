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