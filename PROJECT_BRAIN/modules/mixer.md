# MixerService - Knowledge Base

## Responsibility
Mixes vocal audio with synthesized speech audio.

## Must Never
- Call speech services
- Call translation services
- Call TTS services
- Download videos

## Dependencies
- audio/
- tts/
- config/
- logger/
- exceptions/
- models/

## Produces
- MixedAudioResult (dataclass with mixed_audio_path, duration)

## Consumers
- Renderer
- LocalizationService

## Thread Safe
YES

## Singleton
NO

## Owner
Audio Team

## Stability Level
★★

## AI Rules
Changing this module requires:
- Audio Review
- MixedAudioResult backward compatibility

## Future
- Add crossfade options
- Add volume normalization
- Add multi-track mixing
