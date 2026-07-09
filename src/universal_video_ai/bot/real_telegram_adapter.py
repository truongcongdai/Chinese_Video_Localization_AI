# src/universal_video_ai/bot/real_telegram_adapter.py
"""
Real Telegram bot adapter using python-telegram-bot library.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, List, Optional
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

__all__ = ["RealTelegramAdapter"]

_logger = logging.getLogger(__name__)


class RealTelegramAdapter:
    """
    Real Telegram bot adapter using python-telegram-bot.
    
    Integrates with TelegramBot to handle actual Telegram messages.
    """

    def __init__(self, token: Optional[str] = None, logger: Optional[logging.Logger] = None) -> None:
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        
        self.logger = logger or _logger
        self._handlers: dict[str, Callable[[int, List[str]], None]] = {}
        self._application: Optional[Application] = None
        self._running = False

    def register_command(self, command: str, handler: Callable[[int, List[str]], None]) -> None:
        """Register a command handler."""
        self._handlers[command] = handler
        self.logger.debug("Registered command: %s", command)

    def send_message(self, chat_id: int, text: str) -> None:
        """Send a message to a chat."""
        if self._application:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # If loop is already running, create a task
                asyncio.create_task(self._application.bot.send_message(chat_id=chat_id, text=text))
            except RuntimeError:
                # No running loop, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                async def _send():
                    await self._application.bot.send_message(chat_id=chat_id, text=text)
                
                loop.run_until_complete(_send())
        else:
            self.logger.warning("Cannot send message: application not initialized")

    async def _handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming Telegram command."""
        if not update.message or not update.message.text:
            return
        
        chat_id = update.message.chat_id
        text = update.message.text
        
        # Parse command and arguments
        parts = text.split()
        if not parts:
            return
        
        command = parts[0].lstrip('/')  # Remove leading slash
        args = parts[1:] if len(parts) > 1 else []
        
        self.logger.info("Received command: %s from chat_id: %s", command, chat_id)
        
        # Find and call handler
        handler = self._handlers.get(command)
        if handler:
            try:
                await handler(chat_id, args)
            except Exception as exc:
                self.logger.exception("Error handling command %s: %s", command, exc)
                await update.message.reply_text(f"Error: {exc}")
        else:
            self.logger.warning("No handler for command: %s", command)
            await update.message.reply_text(f"Unknown command: /{command}")

    def start(self) -> None:
        """Start the Telegram bot."""
        if self._running:
            self.logger.warning("Bot already running")
            return
        
        self.logger.info("Starting Telegram bot...")
        
        # Create application
        self._application = Application.builder().token(self.token).build()
        
        # Register command handlers
        for command in self._handlers.keys():
            self._application.add_handler(CommandHandler(command, self._handle_command))
        
        # Start the bot
        self._application.run_polling(allowed_updates=Update.ALL_TYPES)
        self._running = True
        self.logger.info("Telegram bot started")

    def stop(self) -> None:
        """Stop the Telegram bot."""
        if not self._running:
            return
        
        self.logger.info("Stopping Telegram bot...")
        if self._application:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            async def _stop():
                await self._application.stop()
                await self._application.shutdown()
            
            loop.run_until_complete(_stop())
        
        self._running = False
        self.logger.info("Telegram bot stopped")
