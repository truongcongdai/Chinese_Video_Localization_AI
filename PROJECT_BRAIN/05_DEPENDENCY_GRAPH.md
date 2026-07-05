# Universal Video AI - Dependency Graph

## Purpose
This document defines the allowed dependency relationships between modules. AI developers MUST follow this graph - no bypassing allowed.

## Dependency Rules

### Rule 1: Unidirectional Flow
Dependencies flow DOWN the graph. Upper layers depend on lower layers. Lower layers NEVER depend on upper layers.

### Rule 2: No Circular Dependencies
No module may depend on another module that depends on it (directly or indirectly).

### Rule 3: Protocol-Based Boundaries
Cross-module dependencies must go through protocols, never concrete implementations.

## Dependency Layers (Top to Bottom)

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Adapters (External Integration)                     │
│ bot/, api/, webhook/                                         │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Orchestrators (Cross-Service Coordination)          │
│ orchestrator/, jobs/                                         │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Services (Business Logic)                           │
│ downloader/, speech/, translate/, tts/, mixer/, render/      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Backends (External API Implementations)              │
│ speech/whisper.py, translate/translator.py, tts/tts.py       │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: Core (Shared Infrastructure)                        │
│ config/, exceptions/, logger/, models/, database/           │
└─────────────────────────────────────────────────────────────┘
```

## Allowed Dependencies

### Layer 1 (Adapters) MAY Depend On:
- Layer 2 (Orchestrators)
- Layer 3 (Services)
- Layer 5 (Core)

**Examples**:
- `TelegramBot` → `LocalizationService`
- `TelegramBot` → `DownloadService`
- `API Handler` → `JobService`
- `WebhookDispatcher` → `database/`

**FORBIDDEN**:
- `TelegramBot` → `WhisperBackend` (must go through SpeechService)
- `API Handler` → `Translator` (must go through TranslateService)

### Layer 2 (Orchestrators) MAY Depend On:
- Layer 3 (Services)
- Layer 5 (Core)

**Examples**:
- `LocalizationService` → `DownloadService`
- `LocalizationService` → `SpeechService`
- `LocalizationService` → `TranslateService`
- `JobService` → `LocalizationService`

**FORBIDDEN**:
- `LocalizationService` → `TelegramBot` (circular dependency)
- `JobService` → `bot/` (wrong direction)

### Layer 3 (Services) MAY Depend On:
- Layer 4 (Backends - via protocols only)
- Layer 5 (Core)

**Examples**:
- `SpeechService` → `SpeechBackend` (protocol)
- `TranslateService` → `TranslateBackend` (protocol)
- `TTSService` → `TTS` (protocol)
- `DownloadService` → `config/`

**FORBIDDEN**:
- `SpeechService` → `WhisperTranscriber` (must use protocol)
- `TranslateService` → `GoogleTranslator` (must use protocol)
- `DownloadService` → `bot/` (wrong direction)

### Layer 4 (Backends) MAY Depend On:
- Layer 5 (Core)
- External libraries (whisper, googletrans, edge-tts)

**Examples**:
- `WhisperBackend` → `whisper` (external lib)
- `EdgeTTS` → `subprocess` (standard lib)
- `GoogleTranslator` → `googletrans` (external lib)

**FORBIDDEN**:
- `WhisperBackend` → `SpeechService` (wrong direction)
- `EdgeTTS` → `bot/` (wrong direction)

### Layer 5 (Core) MAY Depend On:
- Standard library only
- External infrastructure libraries (pathlib, logging, etc.)

**Examples**:
- `config/` → `pathlib`
- `logger/` → `logging`
- `exceptions/` → (nothing)

**FORBIDDEN**:
- `config/` → `downloader/` (core must be independent)
- `exceptions/` → `speech/` (core must be independent)

## Service-Specific Dependency Graphs

### Localization Pipeline
```
LocalizationService (orchestrator)
    ↓
DownloadService
    ↓
AudioPipeline
    ↓
SpeechService → SpeechBackend (WhisperBackend)
    ↓
TranslateService → TranslateBackend (TranslatorBackend)
    ↓
TTSService → TTS (EdgeTTS)
    ↓
MixerService
    ↓
Renderer
```

### Job Processing
```
JobService
    ↓
LocalizationService
    ↓ (same as above)
```

### Bot Command Flow
```
TelegramBot (adapter)
    ↓
DownloadService OR LocalizationService
    ↓ (same as above)
```

## Forbidden Dependency Patterns

### ❌ Pattern 1: Bypass Service Layer
```python
# FORBIDDEN
class TelegramBot:
    def _handle_transcribe(self, audio_path: Path):
        # Direct backend call - FORBIDDEN
        backend = WhisperBackend()
        text = backend.transcribe(audio_path)

# CORRECT
class TelegramBot:
    def __init__(self, speech_service: SpeechService):
        self.speech_service = speech_service

    def _handle_transcribe(self, audio_path: Path):
        # Go through service layer
        text = self.speech_service.transcribe(audio_path)
```

### ❌ Pattern 2: Upward Dependency
```python
# FORBIDDEN
class DownloadService:
    def __init__(self):
        self.bot = TelegramBot()  # Upward dependency

# CORRECT
class TelegramBot:
    def __init__(self, download_service: DownloadService):
        self.download_service = download_service  # Downward dependency
```

### ❌ Pattern 3: Circular Dependency
```python
# FORBIDDEN
# LocalizationService depends on JobService
class LocalizationService:
    def __init__(self, job_service: JobService):
        self.job_service = job_service

# JobService depends on LocalizationService
class JobService:
    def __init__(self, localization_service: LocalizationService):
        self.localization_service = localization_service

# CORRECT - Use callback pattern
class JobService:
    def run_job_async(self, job_id: str, callback: Callable):
        # LocalizationService passed as callback
        thread = Thread(target=callback, args=(job_id,))
        thread.start()
```

### ❌ Pattern 4: Concrete Implementation Dependency
```python
# FORBIDDEN
class SpeechService:
    def __init__(self):
        self.backend = WhisperBackend()  # Concrete implementation

# CORRECT
class SpeechService:
    def __init__(self, backend: SpeechBackend):  # Protocol
        self.backend = backend
```

## Module Dependency Matrix

| Module | downloader | audio | speech | translate | tts | mixer | render | orchestrator | jobs | bot | api | database | config | exceptions | logger |
|--------|------------|-------|--------|-----------|-----|-------|--------|--------------|------|-----|-----|----------|--------|------------|--------|
| downloader | - | ✓ | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| audio | ✓ | - | ✓ | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| speech | - | - | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| translate | - | - | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| tts | - | - | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| mixer | - | ✓ | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| render | - | - | - | - | - | ✓ | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| orchestrator | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | - | - | - | - | - | ✓ | ✓ | ✓ |
| jobs | - | - | - | - | - | - | - | ✓ | - | - | - | ✓ | ✓ | ✓ | ✓ |
| bot | ✓ | - | ✓ | - | - | - | - | ✓ | ✓ | - | - | ✓ | ✓ | ✓ | ✓ |
| api | - | - | - | - | - | - | - | ✓ | ✓ | - | - | ✓ | ✓ | ✓ | ✓ |
| webhook | - | - | - | - | - | - | - | - | ✓ | - | - | ✓ | ✓ | ✓ | ✓ |
| database | - | - | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ | ✓ |
| config | - | - | - | - | - | - | - | - | - | - | - | - | - | ✓ | ✓ |
| exceptions | - | - | - | - | - | - | - | - | - | - | - | - | - | - | ✓ |
| logger | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |

**Legend**:
- ✓ = Allowed dependency
- - = No dependency

## Context Window Rules for AI

When working on a specific milestone, AI should ONLY read:

### Milestone 1 (Dummy Backend)
**Allowed to read**:
- orchestrator/
- speech/
- translate/
- tts/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/ (read only if necessary)
- database/ (read only if necessary)
- deploy/ (not needed)

### Milestone 2 (Whisper)
**Allowed to read**:
- speech/
- audio/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- translate/
- tts/
- api/
- deploy/

### Milestone 3 (Translation)
**Allowed to read**:
- translate/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- speech/
- tts/
- api/
- deploy/

### Milestone 4 (TTS)
**Allowed to read**:
- tts/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- speech/
- translate/
- api/
- deploy/

### Milestone 5 (Demucs)
**Allowed to read**:
- audio/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- speech/
- translate/
- tts/
- api/
- deploy/

### Milestone 6 (Job Queue)
**Allowed to read**:
- jobs/
- orchestrator/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/ (read only for integration)
- speech/
- translate/
- tts/
- api/

### Milestone 7 (Monitoring)
**Allowed to read**:
- monitoring/
- jobs/
- orchestrator/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- speech/
- translate/
- tts/
- api/

### Milestone 8 (Webhook)
**Allowed to read**:
- webhook/
- jobs/
-database/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- speech/
- translate/
- tts/
- api/

### Milestone 9 (Admin API)
**Allowed to read**:
- api/
- jobs/
- database/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- bot/
- speech/
- translate/
- tts/
- webhook/

### Milestone 10 (Production)
**Allowed to read**:
- Dockerfile
- docker-compose.prod.yml
- nginx.conf
- scripts/
- tests/
- PROJECT_BRAIN/

**Forbidden to read**:
- Core application code (only optimizations)

## Enforcement

These dependency rules are enforced through:
1. Code review (check for violations)
2. AI development guidelines (09_AI_RULES.md)
3. Module map (06_MODULE_MAP.md)
4. Architecture documentation (01_ARCHITECTURE.md)

Violations will result in:
- Code review rejection
- Requirement to refactor
- Architecture review for pattern changes

This dependency graph ensures clean architecture, testability, and maintainability.
