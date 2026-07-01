# src/universal_video_ai/bot/telegram_bot.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import logging
from typing import Callable, Dict, List, Optional, Set

from universal_video_ai.config import TEMP_DIR
from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.validator import UrlValidator
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.orchestrator.service import LocalizationService
from universal_video_ai.database import DatabaseManager

__all__ = ["TelegramBot", "TelegramAdapter", "MockAdapter"]

_logger = logging.getLogger(__name__)


class TelegramAdapter:
    """
    Adapter protocol used by TelegramBot.

    Implementations should:
    - register_command(command: str, handler: Callable[[int, List[str]], None])
    - send_message(chat_id: int, text: str) -> None
    - start() -> None
    - stop() -> None
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
    High-level Telegram bot that exposes commands and delegates tasks.
    """
    adapter: TelegramAdapter
    download_service: DownloadService
    localization_service: Optional[LocalizationService] = None
    database_manager: Optional[DatabaseManager] = None
    admin_chat_ids: Optional[Set[int]] = None
    output_dir: Path | str = TEMP_DIR
    validator: UrlValidator | None = None
    logger: Optional[logging.Logger] = None
    _job_to_chat: Dict[str, int] = field(default_factory=dict)

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
        self.adapter.register_command("localize", self._handle_localize)
        self.adapter.register_command("credits", self._handle_credits)

        # Admin commands
        self.adapter.register_command("addcredits", self._handle_addcredits)
        self.adapter.register_command("setcredits", self._handle_setcredits)

        self.logger.debug("TelegramBot initialized; handlers registered for start/status/download/localize/credits/addcredits/setcredits")

    # ---- Admin helpers ----
    def _is_admin(self, chat_id: int) -> bool:
        if not self.admin_chat_ids:
            return False
        return chat_id in self.admin_chat_ids

    # ---- Handlers ----
    def _handle_start(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /start command.
        """
        text = (
            "🎬 Universal Video AI Bot\n"
            "Commands:\n"
            "/download <url> - download video only\n"
            "/localize <url> - full pipeline (transcribe → translate → TTS → render) [cost: 1 credit]\n"
            "/credits - show your credit balance\n"
            "/status - show bot status\n"
        )
        self.adapter.send_message(chat_id, text)
        self.logger.info("Handled /start for chat=%s", chat_id)

    def _handle_status(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /status command.
        """
        localization_available = self.localization_service is not None
        text = (
            "Bot is running ✓\n"
            f"Localization: {'✓ Enabled' if localization_available else '✗ Disabled'}\n"
        )
        self.adapter.send_message(chat_id, text)
        self.logger.info("Handled /status for chat=%s", chat_id)

    def _handle_download(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /download <url> command.
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
            self.adapter.send_message(chat_id, f"❌ Invalid URL: {exc}")
            return

        # Acknowledge start
        self.adapter.send_message(chat_id, f"⏳ Starting download: {url}")
        self.logger.info("Starting download for chat=%s url=%s", chat_id, url)

        try:
            result: DownloadResult = self.download_service.download(url=url, output_dir=self.output_dir)
        except Exception as exc:
            self.logger.exception("Download failed for url=%s", url)
            self.adapter.send_message(chat_id, f"❌ Download failed: {exc}")
            return

        # Send final status
        if getattr(result, "success", False):
            msg = f"✓ Download completed!\n📽️ {result.title or 'untitled'}\nPlatform: {result.platform}\nFile: {result.video_path}"
            self.adapter.send_message(chat_id, msg)
            self.logger.info("Download succeeded for chat=%s url=%s", chat_id, url)
        else:
            self.adapter.send_message(chat_id, f"❌ Download failed for URL: {url}")
            self.logger.info("Download reported failure for chat=%s url=%s", chat_id, url)

    def _handle_localize(self, chat_id: int, args: List[str]) -> None:
        """
        Handle /localize <url> command.

        Costs: 1 credit per localization job (checked and deducted via DatabaseManager).
        """
        if self.localization_service is None:
            self.adapter.send_message(chat_id, "❌ Localization service not available")
            self.logger.warning("Localize requested but no LocalizationService injected, chat=%s", chat_id)
            return

        if not args:
            self.adapter.send_message(chat_id, "Usage: /localize <video_url>")
            self.logger.debug("Localize called with no args by chat=%s", chat_id)
            return

        url = args[0].strip()
        self.logger.debug("Localize requested by chat=%s for url=%s", chat_id, url)

        # Validate URL
        try:
            self.validator.validate_or_raise(url)
        except Exception as exc:
            self.logger.warning("Invalid URL provided by chat=%s: %s", chat_id, exc)
            self.adapter.send_message(chat_id, f"❌ Invalid URL: {exc}")
            return

        # Check credits
        if self.database_manager:
            user_credit = self.database_manager.get_user_credits(chat_id)
            if user_credit.credits < 1.0:
                self.adapter.send_message(
                    chat_id,
                    f"❌ Insufficient credits!\nCurrent balance: {user_credit.credits:.1f}\nRequired: 1.0\nContact admin for credits."
                )
                self.logger.warning("User %s attempted localization with insufficient credits", chat_id)
                return

            # Try to deduct
            ok = self.database_manager.deduct_credits(chat_id, 1.0)
            if not ok:
                self.adapter.send_message(chat_id, "❌ Failed to deduct credits. Please try again later.")
                self.logger.error("Failed to deduct credits for user %s", chat_id)
                return
            # Inform user of balance
            new_balance = self.database_manager.get_user_credits(chat_id)
            self.adapter.send_message(chat_id, f"🪙 1 credit deducted. Remaining: {new_balance.credits:.1f}")

        # Acknowledge start
        self.adapter.send_message(
            chat_id,
            f"⏳ Localizing video (transcribe → translate → TTS → render):\n{url}\n\nThis may take several minutes..."
        )
        self.logger.info("Starting localization for chat=%s url=%s", chat_id, url)

        try:
            output_dir = Path(self.output_dir) / f"localize_{chat_id}"
            output_dir.mkdir(parents=True, exist_ok=True)

            result = self.localization_service.localize(url, output_dir)

            # Send final status
            if result.final_video_path and result.final_video_path.exists():
                msg = (
                    f"✓ Localization completed!\n"
                    f"📽️ Final video: {result.final_video_path.name}\n"
                    f"Transcript: {len(result.audio_pipeline_result.transcript) if result.audio_pipeline_result.transcript else 0} chars\n"
                    f"Translation: {'✓' if result.translated_text else '✗'}\n"
                    f"Subtitles: {'✓' if result.subtitle_segments else '✗'}\n"
                    f"File: {result.final_video_path}"
                )
                self.adapter.send_message(chat_id, msg)
                self.logger.info("Localization succeeded for chat=%s url=%s", chat_id, url)
            else:
                self.adapter.send_message(chat_id, "❌ Localization completed but no final video generated")
                self.logger.warning("Localization succeeded but no final_video_path for chat=%s", chat_id)

        except Exception as exc:
            self.logger.exception("Localization failed for url=%s", url)
            self.adapter.send_message(chat_id, f"❌ Localization failed: {exc}")

    def _handle_credits(self, chat_id: int, args: List[str]) -> None:
        """Handle /credits command to show balance."""
        if not self.database_manager:
            self.adapter.send_message(chat_id, "Credit system not available")
            return

        # If admin includes a user id, show that user's balance; otherwise show self
        if args:
            try:
                target_user = int(args[0])
            except Exception:
                self.adapter.send_message(chat_id, "Usage: /credits [user_id]")
                return
            # Only admins may view other user's balance
            if not self._is_admin(chat_id):
                self.adapter.send_message(chat_id, "❌ Permission denied to view other user's credits")
                return
            user_credit = self.database_manager.get_user_credits(target_user)
            text = (
                f"💳 Credits for user {target_user}\n"
                f"Current: {user_credit.credits:.1f} credits\n"
                f"Total Used: {user_credit.total_used:.1f} credits\n"
            )
            self.adapter.send_message(chat_id, text)
            return

        user_credit = self.database_manager.get_user_credits(chat_id)
        text = (
            f"💳 Credit Balance\n"
            f"Current: {user_credit.credits:.1f} credits\n"
            f"Total Used: {user_credit.total_used:.1f} credits\n"
            f"Cost: 1 credit per localization\n"
        )
        self.adapter.send_message(chat_id, text)
        self.logger.info("Showed credits for user %s: %.1f", chat_id, user_credit.credits)

    # ---- Admin handlers ----
    def _handle_addcredits(self, chat_id: int, args: List[str]) -> None:
        """Admin command: /addcredits <user_id> <amount>"""
        if not self._is_admin(chat_id):
            self.adapter.send_message(chat_id, "❌ Permission denied")
            return

        if len(args) < 2:
            self.adapter.send_message(chat_id, "Usage: /addcredits <user_id> <amount>")
            return

        try:
            target = int(args[0])
            amount = float(args[1])
        except Exception:
            self.adapter.send_message(chat_id, "Invalid arguments; user_id must be int, amount must be number")
            return

        if not self.database_manager:
            self.adapter.send_message(chat_id, "Database not configured")
            return

        self.database_manager.add_credits(target, amount)
        updated = self.database_manager.get_user_credits(target)
        self.adapter.send_message(chat_id, f"Added {amount:.2f} credits to user {target}. New balance: {updated.credits:.2f}")

    def _handle_setcredits(self, chat_id: int, args: List[str]) -> None:
        """Admin command: /setcredits <user_id> <amount>"""
        if not self._is_admin(chat_id):
            self.adapter.send_message(chat_id, "❌ Permission denied")
            return

        if len(args) < 2:
            self.adapter.send_message(chat_id, "Usage: /setcredits <user_id> <amount>")
            return

        try:
            target = int(args[0])
            amount = float(args[1])
        except Exception:
            self.adapter.send_message(chat_id, "Invalid arguments; user_id must be int, amount must be number")
            return

        if not self.database_manager:
            self.adapter.send_message(chat_id, "Database not configured")
            return

        self.database_manager.set_user_credits(target, amount)
        updated = self.database_manager.get_user_credits(target)
        self.adapter.send_message(chat_id, f"Set credits for user {target} to {updated.credits:.2f}")

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