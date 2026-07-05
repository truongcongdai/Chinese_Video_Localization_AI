# Renderer - Knowledge Base

## Responsibility
Renders final video with burned-in subtitles and mixed audio.

## Must Never
- Call speech services
- Call translation services
- Call TTS services
- Download videos

## Dependencies
- mixer/
- timeline/
- config/
- logger/
- exceptions/
- models/

## Produces
- RenderResult (dataclass with video_path, duration, quality)

## Consumers
- LocalizationService
- JobService
- TelegramBot

## Thread Safe
YES

## Singleton
NO

## Owner
Render Team

## Stability Level
★★

## AI Rules
Changing this module requires:
- Architecture Review
- RenderResult backward compatibility

## Future
- Support more codecs
- Add subtitle styling options
- Add watermark support
