# Universal Video AI - Module Map

## Purpose
This document defines module boundaries, permissions, and what AI developers can and cannot modify in each module.

## Module Classification

### Frozen Modules (Architecture Freeze)
These modules are COMPLETE and IMMUTABLE. AI developers CANNOT modify:
- Class names
- Method names
- Constructor signatures
- Dataclass fields
- Protocol definitions
- Public interfaces

**ALLOWED**:
- Add new private methods
- Add new implementations of existing protocols
- Fix bugs without changing signatures
- Improve logging
- Add type hints (if missing)

### Mutable Modules (Active Development)
These modules can be enhanced according to milestone requirements.

## Module Details

### config/
**Status**: FROZEN
**Purpose**: Configuration management
**Files**:
- `config.py` - Configuration structure

**Immutable**:
- All configuration fields
- Configuration loading logic

**Allowed Changes**:
- Add new configuration fields
- Add validation logic

**Forbidden Changes**:
- Remove existing configuration fields
- Change configuration structure

---

### exceptions/
**Status**: FROZEN
**Purpose**: Exception hierarchy
**Files**:
- `exceptions.py` - Base exception class

**Immutable**:
- All exception class names
- Exception hierarchy structure

**Allowed Changes**:
- Add new exception classes
- Add exception attributes

**Forbidden Changes**:
- Remove existing exception classes
- Change exception inheritance

---

### logger/
**Status**: FROZEN
**Purpose**: Logging infrastructure
**Files**:
- `logger.py` - Logger setup

**Immutable**:
- Logger setup function signature
- Log level constants

**Allowed Changes**:
- Add new log handlers
- Improve log formatting

**Forbidden Changes**:
- Change logger initialization logic

---

### models/
**Status**: FROZEN
**Purpose**: Shared data models
**Files**:
- (Various model files)

**Immutable**:
- All dataclass field names
- All dataclass field types
- Frozen dataclass status

**Allowed Changes**:
- Add new fields to dataclasses (if not frozen)
- Add serialization methods

**Forbidden Changes**:
- Remove dataclass fields
- Change dataclass field types
- Unfreeze frozen dataclasses

---

### downloader/
**Status**: FROZEN
**Purpose**: Video downloading
**Files**:
- `service.py` - DownloadService class
- `download_result.py` - DownloadResult dataclass
- `validator.py` - URL validation
- `platform_detector.py` - Platform detection

**Immutable**:
- `DownloadService` class signature
- `DownloadResult` dataclass fields
- Public method signatures

**Allowed Changes**:
- Add new downloaders for new platforms
- Improve error handling
- Add validation rules

**Forbidden Changes**:
- Change `download()` method signature
- Remove platform support
- Change DownloadResult structure

---

### audio/
**Status**: FROZEN
**Purpose**: Audio extraction and processing
**Files**:
- `pipeline.py` - AudioPipeline class
- `extractor.py` - AudioExtractor class
- `demucs.py` - DemucsProcessor class
- `audio_result.py` - AudioResult dataclass

**Immutable**:
- `AudioPipeline` class signature
- `AudioPipelineConfig` dataclass
- `AudioPipelineResult` dataclass
- `AudioResult` dataclass fields

**Allowed Changes**:
- Enhance Demucs integration
- Add new audio processors
- Improve error handling

**Forbidden Changes**:
- Change pipeline workflow
- Remove pipeline steps
- Change result structures

---

### speech/
**Status**: FROZEN (Protocol), MUTABLE (Implementation)
**Purpose**: Speech-to-text transcription
**Files**:
- `backend.py` - SpeechBackend protocol
- `service.py` - SpeechService class
- `whisper.py` - WhisperTranscriber class
- `exceptions.py` - Speech exceptions

**Immutable**:
- `SpeechBackend` protocol definition
- `SpeechService` class signature
- Exception class names

**Allowed Changes**:
- Add new backend implementations
- Enhance WhisperTranscriber
- Improve error handling
- Add caching

**Forbidden Changes**:
- Change SpeechBackend protocol
- Change SpeechService public methods
- Remove exception classes

---

### translate/
**Status**: FROZEN (Protocol), MUTABLE (Implementation)
**Purpose**: Text translation
**Files**:
- `backend.py` - TranslateBackend protocol
- `service.py` - TranslateService class
- `translator.py` - Translator implementations
- `exceptions.py` - Translation exceptions

**Immutable**:
- `TranslateBackend` protocol definition
- `Translator` protocol definition
- `TranslateService` class signature
- Exception class names

**Allowed Changes**:
- Add new translator implementations (Google, DeepL, etc.)
- Enhance existing translators
- Add caching
- Improve error handling

**Forbidden Changes**:
- Change protocol definitions
- Change TranslateService public methods
- Remove exception classes

---

### tts/
**Status**: FROZEN (Protocol), MUTABLE (Implementation)
**Purpose**: Text-to-speech synthesis
**Files**:
- `backend.py` - TTSBackend protocol
- `service.py` - TTSService class
- `tts.py` - TTS implementations
- `exceptions.py` - TTS exceptions

**Immutable**:
- `TTSBackend` protocol definition
- `TTS` protocol definition
- `TTSService` class signature
- Exception class names

**Allowed Changes**:
- Add new TTS implementations
- Enhance EdgeTTS
- Add voice options
- Improve error handling

**Forbidden Changes**:
- Change protocol definitions
- Change TTSService public methods
- Remove exception classes

---

### mixer/
**Status**: MUTABLE
**Purpose**: Audio mixing
**Files**:
- `service.py` - MixerService class

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Enhance mixing algorithms
- Add new mixing strategies
- Improve error handling

**Forbidden Changes**:
- (None - module is mutable)

---

### render/
**Status**: MUTABLE
**Purpose**: Video rendering
**Files**:
- `renderer.py` - Renderer class
- `quality.py` - Quality presets

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Enhance rendering options
- Add new quality presets
- Improve subtitle burning
- Add new codecs

**Forbidden Changes**:
- (None - module is mutable)

---

### timeline/
**Status**: MUTABLE
**Purpose**: Subtitle timing and alignment
**Files**:
- `service.py` - TimelineService class

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Enhance alignment algorithms
- Add new subtitle formats
- Improve timing accuracy

**Forbidden Changes**:
- (None - module is mutable)

---

### orchestrator/
**Status**: MUTABLE
**Purpose**: End-to-end localization orchestration
**Files**:
- `service.py` - LocalizationService class
- `factory.py` - Service factory

**Immutable**:
- `LocalizationService` class signature
- `LocalizationConfig` dataclass
- `LocalizationResult` dataclass

**Allowed Changes**:
- Add new workflow steps
- Enhance existing steps
- Add new orchestrators
- Improve error recovery

**Forbidden Changes**:
- Change LocalizationService public methods
- Remove workflow steps
- Change config/result structures

---

### jobs/
**Status**: MUTABLE
**Purpose**: Background job processing
**Files**:
- `service.py` - JobService class
- `models.py` - Job dataclasses

**Immutable**:
- `JobService` class signature
- `Job` dataclass fields
- `JobConfig` dataclass fields
- `JobStatus` enum

**Allowed Changes**:
- Add queue implementation
- Add worker implementation
- Add retry logic
- Enhance job tracking

**Forbidden Changes**:
- Change JobService public methods
- Remove job status values
- Change Job dataclass structure

---

### bot/
**Status**: MUTABLE
**Purpose**: Telegram bot integration
**Files**:
- `telegram_bot.py` - TelegramBot class
- `real_telegram_adapter.py` - RealTelegramAdapter
- `mock_adapter.py` - MockAdapter
- `server.py` - Health check server

**Immutable**:
- `TelegramAdapter` protocol
- `TelegramBot` command handler signatures

**Allowed Changes**:
- Add new commands
- Enhance existing handlers
- Add new adapters
- Improve error handling
- Add rate limiting

**Forbidden Changes**:
- Change TelegramAdapter protocol
- Remove existing commands
- Change handler signatures

---

### api/
**Status**: MUTABLE
**Purpose**: API endpoints
**Files**:
- (To be created in Milestone 9)

**Immutable**:
- None (not yet implemented)

**Allowed Changes**:
- (All changes allowed during Milestone 9)

**Forbidden Changes**:
- (None - module not yet frozen)

---

### webhook/
**Status**: MUTABLE
**Purpose**: Webhook notifications
**Files**:
- `service.py` - WebhookService class

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Add webhook delivery
- Add signature verification
- Add retry logic
- Improve error handling

**Forbidden Changes**:
- (None - module is mutable)

---

### database/
**Status**: FROZEN
**Purpose**: Database management
**Files**:
- `__init__.py` - DatabaseManager class

**Immutable**:
- `DatabaseManager` class signature
- Database schema structure

**Allowed Changes**:
- Add new tables
- Add new queries
- Improve error handling
- Add migrations

**Forbidden Changes**:
- Change DatabaseManager public methods
- Remove existing tables
- Change schema structure

---

### cache/
**Status**: MUTABLE
**Purpose**: Caching layer
**Files**:
- (Redis implementation to be added)

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Add Redis implementation
- Add caching strategies
- Improve error handling

**Forbidden Changes**:
- (None - module is mutable)

---

### monitoring/
**Status**: MUTABLE
**Purpose**: Monitoring and metrics
**Files**:
- (To be enhanced in Milestone 7)

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Add metrics collection
- Add alerting
- Add dashboards
- Improve error handling

**Forbidden Changes**:
- (None - module is mutable)

---

### analytics/
**Status**: MUTABLE
**Purpose**: Analytics and reporting
**Files**:
- (To be implemented)

**Immutable**:
- None (not yet implemented)

**Allowed Changes**:
- (All changes allowed)

**Forbidden Changes**:
- (None - module not yet frozen)

---

### cookies/
**Status**: MUTABLE
**Purpose**: Cookie management for downloaders
**Files**:
- (Cookie manager implementation)

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Enhance cookie management
- Add new cookie sources
- Improve error handling

**Forbidden Changes**:
- (None - module is mutable)

---

### temp/
**Status**: MUTABLE
**Purpose**: Temporary file management
**Files**:
- (Temp file utilities)

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Enhance temp file handling
- Add cleanup logic
- Improve error handling

**Forbidden Changes**:
- (None - module is mutable)

---

### utils/
**Status**: MUTABLE
**Purpose**: Shared utilities
**Files**:
- (Utility functions)

**Immutable**:
- None (still evolving)

**Allowed Changes**:
- Add new utilities
- Improve existing utilities
- Add validation helpers

**Forbidden Changes**:
- (None - module is mutable)

---

## Permission Matrix

| Module | Add Class | Add Method | Change Signature | Remove | Rename |
|--------|----------|------------|------------------|--------|--------|
| config | ✓ | ✓ | ✗ | ✗ | ✗ |
| exceptions | ✓ | ✓ | ✗ | ✗ | ✗ |
| logger | ✓ | ✓ | ✗ | ✗ | ✗ |
| models | ✓ | ✓ | ✗ | ✗ | ✗ |
| downloader | ✓ | ✓ | ✗ | ✗ | ✗ |
| audio | ✓ | ✓ | ✗ | ✗ | ✗ |
| speech (protocol) | ✗ | ✗ | ✗ | ✗ | ✗ |
| speech (impl) | ✓ | ✓ | ✗ | ✗ | ✗ |
| translate (protocol) | ✗ | ✗ | ✗ | ✗ | ✗ |
| translate (impl) | ✓ | ✓ | ✗ | ✗ | ✗ |
| tts (protocol) | ✗ | ✗ | ✗ | ✗ | ✗ |
| tts (impl) | ✓ | ✓ | ✗ | ✗ | ✗ |
| mixer | ✓ | ✓ | ✓ | ✓ | ✓ |
| render | ✓ | ✓ | ✓ | ✓ | ✓ |
| timeline | ✓ | ✓ | ✓ | ✓ | ✓ |
| orchestrator | ✓ | ✓ | ✗ | ✗ | ✗ |
| jobs | ✓ | ✓ | ✗ | ✗ | ✗ |
| bot | ✓ | ✓ | ✗ | ✗ | ✗ |
| api | ✓ | ✓ | ✓ | ✓ | ✓ |
| webhook | ✓ | ✓ | ✓ | ✓ | ✓ |
| database | ✓ | ✓ | ✗ | ✗ | ✗ |
| cache | ✓ | ✓ | ✓ | ✓ | ✓ |
| monitoring | ✓ | ✓ | ✓ | ✓ | ✓ |
| analytics | ✓ | ✓ | ✓ | ✓ | ✓ |
| cookies | ✓ | ✓ | ✓ | ✓ | ✓ |
| temp | ✓ | ✓ | ✓ | ✓ | ✓ |
| utils | ✓ | ✓ | ✓ | ✓ | ✓ |

**Legend**:
- ✓ = Allowed
- ✗ = Forbidden

## Change Request Process

For changes to FROZEN modules:

1. **Create ADR**: Document the change in `03_DECISIONS.md`
2. **Architectural Review**: Tech Lead must approve
3. **Update Module Map**: Reflect changes in this document
4. **Update Tests**: Ensure tests cover changes
5. **Update Documentation**: Update relevant docs

For changes to MUTABLE modules:

1. **Follow Milestone**: Ensure change aligns with current milestone
2. **Follow Constitution**: Ensure code follows `04_CONSTITUTION.md`
3. **Add Tests**: Unit and integration tests required
4. **Update Docs**: Update relevant documentation

## Enforcement

These module permissions are enforced through:
1. Code review (check for violations)
2. AI development guidelines (09_AI_RULES.md)
3. Dependency graph (05_DEPENDENCY_GRAPH.md)
4. Architecture documentation (01_ARCHITECTURE.md)

Violations will result in:
- Code review rejection
- Requirement to refactor
- Architectural review for pattern changes

This module map ensures stability while allowing controlled evolution.
