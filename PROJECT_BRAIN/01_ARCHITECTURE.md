# Universal Video AI - Architecture Documentation

## Overview
Universal Video AI is a distributed video localization system that downloads, transcribes, translates, and synthesizes videos from Chinese to Vietnamese (and other languages).

## Architectural Principles

### 1. Protocol-Based Design
All major components use Python Protocols for flexibility:
- Backends can be swapped without changing service layer
- Multiple implementations can coexist (noop, real, mock)
- Test doubles are first-class citizens

### 2. Service Layer Pattern
Business logic lives in service classes:
- Services orchestrate domain operations
- Services delegate to backends via protocols
- Services are dependency-injected
- Services are stateless (except for caching)

### 3. Dependency Injection
All dependencies are injected:
- No singletons
- No global state
- No hardcoded implementations
- Factory pattern for complex object graphs

### 4. Dataclass Over Classes
Data structures use dataclasses:
- Immutable where possible (frozen=True)
- Clear field types
- Built-in serialization methods
- No business logic in dataclasses

### 5. Pathlib Only
All file operations use pathlib.Path:
- No os.path
- No string paths
- Cross-platform compatibility
- Type-safe path operations

### 6. Logging Over Printing
All output uses logging:
- No print() statements
- Structured logging with levels
- Configurable log handlers
- Contextual log messages

## Core Layers

### Layer 1: Protocols (Interfaces)
Location: `src/universal_video_ai/*/backend.py`, `src/universal_video_ai/*/protocols.py`

Purpose: Define contracts between layers

**Key Protocols**:
- `SpeechBackend` - Audio transcription
- `TranslateBackend` - Text translation
- `TTSBackend` - Speech synthesis
- `TTS` - Lower-level TTS interface
- `Translator` - Lower-level translation interface

**Rules**:
- Protocols are IMMUTABLE once defined
- Only add methods, never remove or change signatures
- All implementations must fully implement protocols
- Protocol changes require architectural review

### Layer 2: Backends (Implementations)
Location: `src/universal_video_ai/speech/whisper.py`, `src/universal_video_ai/translate/translator.py`, etc.

Purpose: Concrete implementations of protocols

**Examples**:
- `WhisperBackend` - OpenAI Whisper transcription
- `NoOpTranslator` - Placeholder translation
- `EdgeTTS` - Microsoft Edge TTS
- `GoogleTranslator` - Google Translate API

**Rules**:
- Multiple backends can implement same protocol
- Backends are swappable via configuration
- Backends handle external API calls
- Backends convert external errors to domain exceptions

### Layer 3: Services (Business Logic)
Location: `src/universal_video_ai/*/service.py`

Purpose: Orchestrate domain operations

**Key Services**:
- `DownloadService` - Video downloading
- `SpeechService` - Transcription orchestration
- `TranslateService` - Translation orchestration
- `TTSService` - Speech synthesis orchestration
- `LocalizationService` - End-to-end pipeline
- `JobService` - Background job management
- `TelegramBot` - Bot command handling

**Rules**:
- Services are stateless (except injected dependencies)
- Services delegate to backends via protocols
- Services handle business logic and validation
- Services convert backend exceptions to user-facing errors
- Service public methods are IMMUTABLE signatures

### Layer 4: Orchestrators (Cross-Service Coordination)
Location: `src/universal_video_ai/orchestrator/service.py`

Purpose: Coordinate multiple services

**Key Orchestrator**:
- `LocalizationService` - Full video localization pipeline

**Rules**:
- Orchestrators inject multiple services
- Orchestrators define workflow steps
- Orchestrators handle error recovery
- Orchestrators provide progress tracking

### Layer 5: Adapters (External Integration)
Location: `src/universal_video_ai/bot/`, `src/universal_video_ai/api/`

Purpose: Bridge external systems to internal services

**Examples**:
- `TelegramAdapter` - Telegram bot interface
- `MockAdapter` - Test double for Telegram
- `RealTelegramAdapter` - Production Telegram implementation

**Rules**:
- Adapters translate external protocols to internal
- Adapters handle external authentication
- Adapters are protocol-based for testability
- Adapters do not contain business logic

## Data Flow

### Typical Request Flow
```
External Request (Telegram/API)
    ↓
Adapter (TelegramAdapter)
    ↓
Bot (TelegramBot)
    ↓
Service (LocalizationService)
    ↓
Backend (WhisperBackend, TranslateBackend, TTSBackend)
    ↓
External API (Whisper, Google, EdgeTTS)
```

### Job Processing Flow
```
User submits job
    ↓
JobService.create_job()
    ↓
JobService.run_job_async()
    ↓
Worker thread executes callback
    ↓
LocalizationService.localize()
    ↓
DownloadService.download()
    ↓
AudioPipeline.process()
    ↓
SpeechService.transcribe()
    ↓
TranslateService.translate()
    ↓
TTSService.synthesize()
    ↓
MixerService.mix()
    ↓
Renderer.render()
    ↓
JobService.update_job(completed)
```

## Module Boundaries

### Frozen Modules (Architecture Freeze)
These modules are COMPLETE and IMMUTABLE:

**audio/**
- Audio extraction, Demucs separation
- AudioPipeline orchestrates extraction → demucs → transcription
- Frozen interfaces: AudioPipelineConfig, AudioPipelineResult

**speech/**
- Speech-to-text transcription
- SpeechService uses SpeechBackend protocol
- Frozen interfaces: SpeechBackend, SpeechService

**translate/**
- Text translation
- TranslateService uses TranslateBackend protocol
- Frozen interfaces: TranslateBackend, Translator, TranslateService

**tts/**
- Text-to-speech synthesis
- TTSService uses TTS protocol
- Frozen interfaces: TTS, TTSBackend, TTSService

**database/**
- SQLite database management
- User credits, job tracking
- Frozen interfaces: DatabaseManager

**downloader/**
- Video downloading from multiple platforms
- Platform detection, URL validation
- Frozen interfaces: DownloadService, DownloadResult

**models/**
- Shared data models
- Frozen dataclass definitions

**exceptions/**
- Exception hierarchy
- Frozen exception classes

**config/**
- Configuration management
- Frozen configuration structure

### Mutable Modules (Allowed to Change)
These modules can be enhanced:

**bot/**
- Add new commands
- Enhance existing handlers
- Add new adapters

**orchestrator/**
- Add new workflow steps
- Enhance LocalizationService
- Add new orchestrators

**jobs/**
- Add queue implementation
- Add worker implementation
- Enhance job tracking

**api/**
- Add new endpoints
- Add authentication
- Add rate limiting

**monitoring/**
- Add metrics collection
- Add alerting
- Add dashboards

**webhook/**
- Add webhook delivery
- Add signature verification
- Add retry logic

**cache/**
- Add Redis implementation
- Add caching strategies

**mixer/**
- Enhance audio mixing
- Add new mixing strategies

**render/**
- Enhance video rendering
- Add new rendering options

**timeline/**
- Enhance subtitle timing
- Add new alignment algorithms

## Technology Stack

### Core
- Python 3.11+
- Type hints (100% coverage required)
- dataclasses for data structures
- pathlib for file operations

### External Dependencies
- yt-dlp - Video downloading
- ffmpeg - Audio/video processing
- whisper - Speech recognition
- edge-tts - Text-to-speech
- googletrans - Translation (optional)
- demucs - Audio separation (optional)

### Infrastructure
- SQLite - Database
- Redis - Queue and cache (optional)
- Docker - Containerization
- nginx - Reverse proxy (production)

### Development
- pytest - Testing
- mypy - Type checking
- ruff - Linting
- black - Formatting

## Non-Functional Requirements

### Performance
- Single video localization: <5 minutes (typical)
- Concurrent job processing: 10+ workers
- API response time: <200ms (p95)
- Database queries: <50ms (p95)

### Reliability
- Job success rate: >95%
- System uptime: >99.5%
- Data durability: SQLite with backups
- Graceful degradation on backend failures

### Scalability
- Horizontal scaling via worker processes
- Queue-based job processing
- Stateless services (except database/cache)
- CDN for static file delivery

### Security
- API key management via environment
- Webhook signature verification
- Rate limiting per user
- Admin authentication
- SQL injection prevention (ORM)

### Maintainability
- Protocol-based design for flexibility
- Comprehensive test coverage (>80%)
- Clear module boundaries
- Documentation for all public APIs
- Architecture decision records

## Deployment Architecture

### Development
- Local execution via CLI
- Mock adapters for testing
- SQLite database
- No external services required

### Production
- Docker containers
- Redis for queue/cache
- Multiple worker processes
- nginx reverse proxy
- Monitoring with Prometheus/Grafana
- Log aggregation

## Key Architectural Decisions

See `03_DECISIONS.md` for detailed architecture decision records.

## Evolution Strategy

The architecture is designed to evolve:
1. New backends can be added without changing services
2. New services can be added without breaking existing ones
3. New adapters can be added for new platforms
4. Protocols ensure backward compatibility
5. Module boundaries prevent architectural drift

This architecture supports the 10-milestone roadmap while maintaining stability and flexibility.
