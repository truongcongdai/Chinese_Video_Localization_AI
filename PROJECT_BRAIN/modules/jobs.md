# JobService - Knowledge Base

## Responsibility
Manages background job processing for video localization tasks.

## Must Never
- Directly call backends (must use service layer)
- Bypass service layer
- Modify frozen module interfaces

## Dependencies
- LocalizationService
- database/
- config/
- logger/
- exceptions/
- models/

## Produces
- Job (dataclass with job_id, status, result, error)
- JobQueue (priority-based job queue)

## Consumers
- API
- TelegramBot
- WebhookService

## Thread Safe
YES

## Singleton
NO (but queue is singleton)

## Owner
Jobs Team

## Stability Level
★★★★

## AI Rules
Changing this module requires:
- Architecture Review
- Job dataclass backward compatible
- JobStatus enum unchanged

## Future
- Add job scheduling
- Add job cancellation
- Add job dependencies
