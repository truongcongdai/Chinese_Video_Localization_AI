# AudioPipeline - Knowledge Base

## Responsibility
Extracts audio from videos and processes audio (vocal separation using Demucs).

## Must Never
- Call speech services directly
- Call translation services
- Modify video files
- Call TTS services

## Dependencies
- DownloadService
- downloader/
- config/
- logger/
- exceptions/
- models/

## Produces
- AudioResult (dataclass with audio_path, vocal_path, instrumental_path)

## Consumers
- SpeechService
- MixerService
- LocalizationService

## Thread Safe
YES

## Singleton
NO

## Owner
Audio Team

## Stability Level
★★★★★ Stable

## AI Rules
Changing this module requires:
- Architecture Review
- AudioResult backward compatibility
- Demucs integration validation

## Future
- Support more audio formats
- Add audio normalization
- Add noise reduction
