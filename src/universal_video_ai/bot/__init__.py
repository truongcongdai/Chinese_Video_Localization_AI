# src/universal_video_ai/bot/__init__.py
"""
Telegram bot package.

Exports:
- TelegramBot
- TelegramAdapter (protocol)
- MockAdapter (testing helper)
"""
from .telegram_bot import TelegramBot, TelegramAdapter, MockAdapter

__all__ = ["TelegramBot", "TelegramAdapter", "MockAdapter"]