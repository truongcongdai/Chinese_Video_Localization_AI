# TTSService - Knowledge Base

## Responsibility
Synthesizes speech from text using TTS backends (EdgeTTS, etc.).

## Must Never
- Call speech services
- Call translation services
- Download videos
- Extract audio

## Dependencies
- TTS (protocol)
- config/
- logger/
- exceptions/
- models/

## Produces
- TTSResult (dataclass with audio_path, voice, duration)

## Consumers
- MixerService
- LocalizationService
- TelegramBot

## Thread Safe
YES

## Singleton
NO

## Owner
TTS Team

## Stability Level
★★★

## AI Rules
Changing this module requires:
- Architecture Review
- TTS protocol unchanged
- TTSResult backward compatible

## Future
- Support more TTS engines
- Add voice cloning
- Add emotion control
