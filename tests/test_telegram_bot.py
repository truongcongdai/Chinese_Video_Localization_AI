# tests/test_telegram_bot.py
from pathlib import Path
import time

import pytest

from universal_video_ai.bot.telegram_bot import TelegramBot, MockAdapter
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.downloader.platform import Platform


class FakeDownloadService:
    """
    Simple fake DownloadService used for tests.
    """

    def __init__(self, succeed: bool = True, delay: float = 0.0):
        self.succeed = succeed
        self.delay = delay
        self.calls = []

    def download(self, url: str, output_dir: Path) -> DownloadResult:
        self.calls.append((url, Path(output_dir)))
        if self.delay:
            time.sleep(self.delay)
        if self.succeed:
            return DownloadResult(
                success=True,
                platform=Platform.YOUTUBE,
                original_url=url,
                final_url=url,
                video_path=Path(output_dir) / "video.mp4",
                title="Fake Title",
                uploader="uploader",
                duration=1.0,
                width=1280,
                height=720,
                filesize=1234,
                extension="mp4",
            )
        return DownloadResult(
            success=False,
            platform=Platform.GENERIC,
            original_url=url,
            final_url=url,
            video_path=Path(output_dir) / "video.mp4",
            title="",
        )


def test_start_and_status_handlers():
    adapter = MockAdapter()
    fake_service = FakeDownloadService()
    bot = TelegramBot(adapter=adapter, download_service=fake_service)

    # Simulate /start
    adapter.simulate_command("start", [], chat_id=42)
    assert adapter.sent_messages, "No messages sent for /start"
    assert adapter.sent_messages[-1][0] == 42
    assert "Universal Video AI Bot" in adapter.sent_messages[-1][1]

    # Simulate /status
    adapter.simulate_command("status", [], chat_id=42)
    assert adapter.sent_messages[-1][0] == 42
    assert "Bot is running" in adapter.sent_messages[-1][1]


def test_download_invalid_url():
    adapter = MockAdapter()
    fake_service = FakeDownloadService()
    bot = TelegramBot(adapter=adapter, download_service=fake_service)

    # Simulate /download with invalid url
    adapter.simulate_command("download", ["not-a-url"], chat_id=7)
    # Should send invalid URL message
    last = adapter.sent_messages[-1]
    assert last[0] == 7
    assert "Invalid URL" in last[1]


def test_download_success_calls_service_and_reports(tmp_path: Path):
    adapter = MockAdapter()
    fake_service = FakeDownloadService(succeed=True)
    bot = TelegramBot(adapter=adapter, download_service=fake_service, output_dir=tmp_path)

    url = "https://youtu.be/dQw4w9WgXcQ"
    adapter.simulate_command("download", [url], chat_id=99)

    # First message is acknowledgement
    assert adapter.sent_messages[0][0] == 99
    assert "Starting download" in adapter.sent_messages[0][1]

    # Final message reports success
    assert any("Download completed" in m for _, m in adapter.sent_messages), adapter.sent_messages

    # Ensure fake_service was called
    assert fake_service.calls and fake_service.calls[0][0] == url


def test_download_service_failure_reports(tmp_path: Path):
    adapter = MockAdapter()
    fake_service = FakeDownloadService(succeed=False)
    bot = TelegramBot(adapter=adapter, download_service=fake_service, output_dir=tmp_path)

    url = "https://youtu.be/dQw4w9WgXcQ"
    adapter.simulate_command("download", [url], chat_id=5)

    # Final message should indicate failure
    assert any("Download failed" in m for _, m in adapter.sent_messages), adapter.sent_messages