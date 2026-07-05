# LocalizationService - Knowledge Base

## Responsibility
Orchestrates end-to-end video localization pipeline (download → transcribe → translate → TTS → mix → render).

## Must Never
- Directly call backends (must use service layer)
- Bypass service layer
- Modify frozen module interfaces

## Dependencies
- DownloadService
- AudioPipeline
- SpeechService
- TranslateService
- TTSService
- MixerService
- Renderer
- config/
- logger/
- exceptions/
- models/

## Produces
- LocalizationResult (dataclass with final_video_path, metadata, steps)

## Consumers
- JobService
- TelegramBot
- API

## Thread Safe
YES

## Singleton
NO

## Owner
Orchestration Team

## Stability Level
★★★★

## AI Rules
Changing this module requires:
- Architecture Review
- LocalizationResult backward compatibility
- Workflow step validation

## Future
- Add parallel pipeline steps
- Add progress callbacks
- Add resume capability
