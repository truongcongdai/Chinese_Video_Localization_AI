# tests/test_telegram_bot.py
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from universal_video_ai.bot.telegram_bot import TelegramBot, MockAdapter
from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.orchestrator.service import LocalizationService, LocalizationResult
from universal_video_ai.audio.pipeline import AudioPipelineResult, AudioResult
from universal_video_ai.database import DatabaseManager


def test_telegram_bot_start_command():
    """Test /start command shows help."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)
    bot = TelegramBot(adapter=adapter, download_service=downloader)

    bot.adapter.simulate_command("start", chat_id=123)

    assert len(adapter.sent_messages) == 1
    chat_id, text = adapter.sent_messages[0]
    assert chat_id == 123
    assert "Universal Video AI Bot" in text
    assert "/download" in text
    assert "/localize" in text


def test_telegram_bot_download_no_args():
    """Test /download with no args shows usage."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)
    bot = TelegramBot(adapter=adapter, download_service=downloader)

    bot.adapter.simulate_command("download", args=[], chat_id=123)

    assert len(adapter.sent_messages) == 1
    _, text = adapter.sent_messages[0]
    assert "usage:" in text.lower()


def test_telegram_bot_download_success(tmp_path: Path):
    """Test successful /download command."""
    adapter = MockAdapter()

    # Mock downloader
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.title = "Test Video"
    download_result.platform = "youtube"
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    bot = TelegramBot(adapter=adapter, download_service=downloader, output_dir=tmp_path)
    bot.adapter.simulate_command("download", args=["https://youtube.com/watch?v=test"], chat_id=123)

    assert len(adapter.sent_messages) >= 2  # acknowledge + result
    last_message = adapter.sent_messages[-1][1]
    assert "Download completed" in last_message or "completed" in last_message.lower()


def test_telegram_bot_localize_success(tmp_path: Path):
    """Test successful /localize command with full pipeline."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)
    localization_service = MagicMock(spec=LocalizationService)

    # Mock localization result
    audio_result_obj = MagicMock()
    audio_result_obj.audio_path = tmp_path / "audio.wav"
    audio_result_obj.duration = 10.0

    audio_result = MagicMock(spec=AudioPipelineResult)
    audio_result.transcript = "Hello world"
    audio_result.audio_result = audio_result_obj

    final_video = tmp_path / "output_final.mp4"
    final_video.write_bytes(b"final video content")

    localization_result = MagicMock(spec=LocalizationResult)
    localization_result.audio_pipeline_result = audio_result
    localization_result.translated_text = "Xin chào thế giới"
    localization_result.subtitle_segments = ["segment1", "segment2"]
    localization_result.final_video_path = final_video

    localization_service.localize.return_value = localization_result

    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        localization_service=localization_service,
        output_dir=tmp_path,
    )

    bot.adapter.simulate_command("localize", args=["https://youtube.com/watch?v=test"], chat_id=123)

    # Should have acknowledge + final result
    assert len(adapter.sent_messages) >= 2
    last_message = adapter.sent_messages[-1][1]
    assert "Localization completed" in last_message or "completed" in last_message.lower()
    assert "Transcript:" in last_message
    assert "Translation:" in last_message
    assert "Subtitles:" in last_message


def test_telegram_bot_localize_no_service():
    """Test /localize when LocalizationService not available."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)

    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        localization_service=None,  # Not available
    )

    bot.adapter.simulate_command("localize", args=["https://youtube.com/watch?v=test"], chat_id=123)

    assert len(adapter.sent_messages) >= 1
    last_message = adapter.sent_messages[-1][1]
    assert "not available" in last_message.lower()


def test_telegram_bot_status_with_localization(tmp_path: Path):
    """Test /status shows localization is enabled."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)
    localization_service = MagicMock(spec=LocalizationService)

    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        localization_service=localization_service,
        output_dir=tmp_path,
    )

    bot.adapter.simulate_command("status", chat_id=123)

    assert len(adapter.sent_messages) == 1
    _, text = adapter.sent_messages[0]
    assert "Enabled" in text
    assert "running" in text.lower()

# --- append to tests/test_telegram_bot.py ---

def test_admin_addcredits_and_setcredits(tmp_path: Path):
    """Admin can add and set credits for other users."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    # Set admin chat id 1
    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        database_manager=manager,
        admin_chat_ids={1},
        output_dir=tmp_path,
    )

    # Ensure user 99 exists with default 3 credits
    manager.get_user_credits(99)

    # Admin adds 5 credits to user 99 (3 + 5 = 8)
    bot.adapter.simulate_command("addcredits", args=["99", "5"], chat_id=1)
    # The last message should confirm
    assert any("Added" in msg or "New balance" in msg for (_, msg) in adapter.sent_messages)

    # Check DB updated (default 3 + 5 = 8)
    uc = manager.get_user_credits(99)
    assert pytest.approx(uc.credits, 0.001) == 8.0

    # Admin sets credits to 2.5
    bot.adapter.simulate_command("setcredits", args=["99", "2.5"], chat_id=1)
    uc2 = manager.get_user_credits(99)
    assert pytest.approx(uc2.credits, 0.001) == 2.5


def test_admin_permission_denied(tmp_path: Path):
    """Non-admin cannot call admin commands."""
    adapter = MockAdapter()
    downloader = MagicMock(spec=DownloadService)
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    # admin_chat_ids does not include 2
    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        database_manager=manager,
        admin_chat_ids={1},
        output_dir=tmp_path,
    )

    # Non-admin (chat_id=2) attempts to addcredits
    bot.adapter.simulate_command("addcredits", args=["100", "1"], chat_id=2)
    # The last message should be permission denied
    assert adapter.sent_messages[-1][1].lower().startswith("❌ permission denied")