# src/universal_video_ai/bot/telegram_bot.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
from typing import Callable, Dict, List, Optional, Protocol

from universal_video_ai.config import TEMP_DIR
from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.validator import UrlValidator
from universal_video_ai.downloader.download_result import DownloadResult

__all__ = ["TelegramBot", "TelegramAdapter", "MockAdapter"]

_logger = logging.getLogger(__name__)


class TelegramAdapter(Protocol):
    """
    Adapter protocol used by TelegramBot.

    Implementations should:
    - register_command(command: str, handler: Callable[[int, List[str]], None])
    - send_message(chat_id: int, text: str) -> None
    - start() -> None
    - stop() -> None

    This keeps the bot implementation independent from any specific telegram client
    library and easy to test.
    """

    def register_command(self, command: str, handler: Callable[[int, List[str]], None]) -> None:
        ...

    def send_message(self, chat_id: int, text: str) -> None:
        ...

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


class MockAdapter:
    """
    Simple in-process adapter useful for unit tests.

    - Handlers are stored in a dict keyed by command string.
    - You can simulate calling a command with `simulate_command`.
    - Sent messages are recorded in `sent_messages`.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[[int, List[str]], None]] = {}
        self.sent_messages: List[tuple[int, str]] = []
        self._running = False

    def register_command(self, command: str, handler: Callable[[int, List[str]], None]) -> None:
        self._handlers[command] = handler

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def simulate_command(self, command: str, args: Optional[List[str]] = None, chat_id: int = 1) -> None:
        """
        Simulate an incoming command from a chat.

        :param command: command name without leading slash (e.g., "download")
        :param args: list of arguments (e.g., ["https://..."])
        :param chat_id: simulated chat id
        """
        handler = self._handlers.get(command)
        if not handler:
            raise RuntimeError(f"No handler registered for command '{command}'")
        handler(chat_id, args or [])


@dataclass
class TelegramBot:
    """
    High-level Telegram bot that exposes basic commands and delegates download tasks
    to a provided DownloadService.

    The bot does not depend directly on any telegram client library; instead it
    uses an adapter implementing `TelegramAdapter` so production code can provide
    a real adapter (wrapping python-telegram-bot or aiogram) and tests can use
    `MockAdapter`.
    """

    adapter: TelegramAdapter
    download_service: DownloadService
    output_dir: Path | str = TEMP_DIR
    validator: UrlValidator | None = None
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        # Normalize types and set defaults
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.validator = self.validator or UrlValidator()
        self.logger = self.logger or _logger

        # Register handlers
        self.adapter.register_command("start", self._handle_start)
        self.adapter.register_command("status", self._handle_status)
        self.adapter.register_command("download", self._handle_download)

        self.logger.debug("TelegramBot initialized; handlers registered for start/status/download")

    # ---- Handlers ----
    def _handle_start(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /start command.
        """
        text = (
            "Universal Video AI Bot\n"
            "Commands:\n"
            "/download <url> - download video and process\n"
            "/status - show bot status\n"
        )
        self.adapter.send_message(chat_id, text)
        self.logger.info("Handled /start for chat=%s", chat_id)

    def _handle_status(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /status command.
        """
        text = "Bot is running"
        self.adapter.send_message(chat_id, text)
        self.logger.info("Handled /status for chat=%s", chat_id)

    def _handle_download(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /download <url> command.

        This method:
        - validates URL
        - calls DownloadService.download(url, output_dir)
        - sends back an acknowledgement and final result message
        """
        if not args:
            self.adapter.send_message(chat_id, "Usage: /download <video_url>")
            self.logger.debug("Download called with no args by chat=%s", chat_id)
            return

        url = args[0].strip()
        self.logger.debug("Download requested by chat=%s for url=%s", chat_id, url)

        # Validate URL
        try:
            self.validator.validate_or_raise(url)
        except Exception as exc:
            self.logger.warning("Invalid URL provided by chat=%s: %s", chat_id, exc)
            self.adapter.send_message(chat_id, f"Invalid URL: {exc}")
            return

        # Acknowledge start
        self.adapter.send_message(chat_id, f"Starting download: {url}")
        self.logger.info("Starting download for chat=%s url=%s", chat_id, url)

        try:
            # Call download service (synchronous). Caller may choose to provide an async wrapper.
            result: DownloadResult = self.download_service.download(url=url, output_dir=self.output_dir)
        except Exception as exc:
            self.logger.exception("Download failed for url=%s", url)
            self.adapter.send_message(chat_id, f"Download failed: {exc}")
            return

        # Send final status
        if getattr(result, "success", False):
            msg = f"Download completed: {result.title or 'untitled'}\nPlatform: {result.platform}\nFile: {result.video_path}"
            self.adapter.send_message(chat_id, msg)
            self.logger.info("Download succeeded for chat=%s url=%s", chat_id, url)
        else:
            self.adapter.send_message(chat_id, f"Download failed for URL: {url}")
            self.logger.info("Download reported failure for chat=%s url=%s", chat_id, url)

    # ---- Lifecycle ----
    def start(self) -> None:
        """
        Start adapter polling / event loop.
        """
        self.logger.info("Starting TelegramBot adapter")
        self.adapter.start()

    def stop(self) -> None:
        """
        Stop adapter polling / event loop.
        """
        self.logger.info("Stopping TelegramBot adapter")
        self.adapter.stop()