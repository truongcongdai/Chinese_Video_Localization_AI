# TranslateService - Knowledge Base

## Responsibility
Translates text between languages using translation backends (Google, DeepL, etc.).

## Must Never
- Call speech services
- Call TTS services
- Download videos
- Extract audio

## Dependencies
- TranslateBackend (protocol)
- config/
- logger/
- exceptions/
- models/

## Produces
- TranslationResult (dataclass with translated_text, source_lang, target_lang)

## Consumers
- TTSService
- LocalizationService
- TelegramBot

## Thread Safe
YES

## Singleton
NO

## Owner
Translation Team

## Stability Level
★★★

## AI Rules
Changing this module requires:
- Architecture Review
- TranslateBackend protocol unchanged
- TranslationResult backward compatible

## Future
- Support more translation engines
- Add context-aware translation
- Add glossary support
