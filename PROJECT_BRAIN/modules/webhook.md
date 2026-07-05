# WebhookService - Knowledge Base

## Responsibility
Sends webhook notifications for job completion events.

## Must Never
- Directly call backends
- Bypass service layer
- Modify frozen module interfaces

## Dependencies
- JobService
- database/
- config/
- logger/
- exceptions/
- models/

## Produces
- WebhookResult (dataclass with status, response_code, retry_count)

## Consumers
- JobService

## Thread Safe
YES

## Singleton
NO

## Owner
Webhook Team

## Stability Level
★

## AI Rules
Changing this module requires:
- Webhook Team Review
- WebhookResult backward compatible

## Future
- Add signature verification
- Add retry with exponential backoff
- Add webhook batching
