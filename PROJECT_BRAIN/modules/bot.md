# TelegramBot - Knowledge Base

## Responsibility
Telegram bot interface for video localization commands.

## Must Never
- Directly call backends (must use service layer)
- Bypass service layer
- Call internal services directly (use LocalizationService)

## Dependencies
- LocalizationService
- DownloadService
- JobService
- database/
- config/
- logger/
- exceptions/
- models/

## Produces
- BotResponse (dataclass with message, status, job_id)

## Consumers
- None (entry point)

## Thread Safe
YES

## Singleton
YES (single bot instance)

## Owner
Bot Team

## Stability Level
★★

## AI Rules
Changing this module requires:
- Bot Team Review
- TelegramAdapter protocol unchanged
- Command handler signatures unchanged

## Future
- Add more commands
- Add rate limiting
- Add user authentication
