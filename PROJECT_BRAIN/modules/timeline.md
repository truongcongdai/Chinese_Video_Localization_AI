# TimelineService - Knowledge Base

## Responsibility
Manages subtitle timing and alignment for video localization.

## Must Never
- Call speech services
- Call translation services
- Call TTS services
- Download videos

## Dependencies
- config/
- logger/
- exceptions/
- models/

## Produces
- TimelineResult (dataclass with segments, total_duration)

## Consumers
- Renderer
- LocalizationService

## Thread Safe
YES

## Singleton
NO

## Owner
Timeline Team

## Stability Level
★★

## AI Rules
Changing this module requires:
- Timeline Team Review
- TimelineResult backward compatible

## Future
- Add auto-alignment
- Support more subtitle formats
- Add timing optimization
