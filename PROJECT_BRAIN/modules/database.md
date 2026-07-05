# DatabaseManager - Knowledge Base

## Responsibility
Manages SQLite database for job storage and metadata.

## Must Never
- Call business logic services
- Directly access other modules
- Modify schema without migration

## Dependencies
- config/
- logger/
- exceptions/
- models/

## Produces
- Database connection
- Query results

## Consumers
- JobService
- TelegramBot
- API
- WebhookService

## Thread Safe
YES (with connection pooling)

## Singleton
YES

## Owner
Database Team

## Stability Level
★★★★★ Stable

## AI Rules
Changing this module requires:
- Database Team Review
- Schema migration required
- Backward compatibility critical

## Future
- Add connection pooling
- Add query optimization
- Add backup/restore
