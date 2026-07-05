# SpeechService - Knowledge Base

## Responsibility
Transcribes audio to text using speech-to-text backends (Whisper, etc.).

## Must Never
- Call translation services
- Call TTS services
- Download videos
- Extract audio

## Dependencies
- SpeechBackend (protocol)
- config/
- logger/
- exceptions/
- models/

## Produces
- TranscriptionResult (dataclass with text, segments, language)

## Consumers
- TranslateService
- LocalizationService
- TelegramBot

## Thread Safe
YES

## Singleton
NO

## Owner
Speech Team

## Stability Level
★★★★

## AI Rules
Changing this module requires:
- Architecture Review
- SpeechBackend protocol unchanged
- TranscriptionResult backward compatible

## Future
- Support more speech engines
- Add speaker diarization
- Add language detection
