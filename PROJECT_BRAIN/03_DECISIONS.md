# Universal Video AI - Architecture Decision Records

## Purpose
This document records significant architectural decisions. Each decision has a unique ID, status, context, and rationale. Once a decision is recorded, it should not be changed without a new ADR.

---

## ADR-001: Use Dependency Injection Instead of Singletons

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
The application needs to manage multiple services (DownloadService, SpeechService, etc.) that depend on each other. Some team members suggested using singleton pattern for easy access.

### Decision
Use dependency injection (DI) for all service dependencies. No singletons or global state.

### Rationale
**Pros**:
- Services are testable (can inject mocks)
- Explicit dependencies (clear what each service needs)
- No hidden global state
- Supports multiple configurations (dev, test, prod)
- Easier to reason about data flow

**Cons**:
- More boilerplate (factory functions)
- Deeper call stacks

**Alternatives Considered**:
1. **Singleton pattern**: Rejected due to testing difficulties and global state
2. **Service locator pattern**: Rejected due to hidden dependencies
3. **Global module**: Rejected due to import-order issues

### Consequences
- All services must accept dependencies via `__init__`
- Factory functions needed for complex object graphs
- Configuration determines which implementations are injected
- Testing is simplified (inject mocks easily)

### Implementation
```python
# ✅ CORRECT
class SpeechService:
    def __init__(self, backend: SpeechBackend, logger: logging.Logger):
        self.backend = backend
        self.logger = logger

# Factory
def create_speech_service(config: Config) -> SpeechService:
    backend = WhisperBackend(config.whisper)
    return SpeechService(backend, logger)
```

---

## ADR-002: Speech Operations Through Service Layer

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
The application needs speech transcription functionality. Some suggested calling Whisper directly from bot or orchestrator.

### Decision
All speech operations must go through SpeechService layer. Direct Whisper calls forbidden outside speech module.

### Rationale
**Pros**:
- Consistent interface regardless of backend (Whisper, Google, etc.)
- Centralized error handling and logging
- Easy to add caching, rate limiting, metrics
- Testable (can mock entire service)
- Business logic stays in service layer

**Cons**:
- Additional layer of indirection
- Slight performance overhead

**Alternatives Considered**:
1. **Direct Whisper calls**: Rejected due to tight coupling
2. **Whisper in orchestrator**: Rejected due to mixing concerns

### Consequences
- SpeechService is the only public API for transcription
- Backends implement SpeechBackend protocol
- Bot/orchestrator only know about SpeechService
- Can swap Whisper for Google Speech without changing callers

### Implementation
```python
# ✅ CORRECT
class LocalizationService:
    def __init__(self, speech_service: SpeechService):
        self.speech_service = speech_service

    def localize(self, url: str):
        transcript = self.speech_service.transcribe(audio_path)
```

---

## ADR-003: Use Pathlib Exclusively for File Operations

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Python offers both os.path and pathlib for file operations. Some team members prefer os.path due to familiarity.

### Decision
Use pathlib.Path exclusively. No os.path usage allowed in new code.

### Rationale
**Pros**:
- Object-oriented API (methods instead of functions)
- Type-safe (Path objects vs strings)
- Cross-platform compatibility
- Cleaner syntax (/ operator for joining)
- Built-in validation

**Cons**:
- Learning curve for developers used to os.path
- Slightly more verbose for simple operations

**Alternatives Considered**:
1. **os.path**: Rejected due to string-based errors
2. **pathlib + os.path mix**: Rejected due to inconsistency

### Consequences
- All file paths are Path objects
- No string manipulation of paths
- Cross-platform compatibility guaranteed
- Type hints work correctly (Path vs str)

### Implementation
```python
# ✅ CORRECT
from pathlib import Path

output_dir = Path("/tmp") / "output" / "video.mp4"
output_dir.parent.mkdir(parents=True, exist_ok=True)

# ❌ FORBIDDEN
import os
output_dir = os.path.join("/tmp", "output", "video.mp4")
os.makedirs(os.path.dirname(output_dir), exist_ok=True)
```

---

## ADR-004: Protocol-Based Design for Backends

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Multiple backends needed (Whisper, Google, noop). Some suggested abstract base classes (ABC).

### Decision
Use Python Protocols (typing.Protocol) for backend interfaces. No ABCs.

### Rationale
**Pros**:
- Structural subtyping (duck typing with types)
- No inheritance required
- Multiple implementations can coexist
- Better IDE support and type checking
- Explicit interface definition

**Cons**:
- Requires Python 3.8+
- Slightly more verbose than duck typing

**Alternatives Considered**:
1. **Abstract Base Classes**: Rejected due to nominal typing requirements
2. **Duck typing only**: Rejected due to lack of explicit contracts
3. **zope.interface**: Rejected due to additional dependency

### Consequences
- All backends implement protocols
- Type checkers can verify protocol compliance
- Easy to add new backends
- Test doubles are first-class citizens

### Implementation
```python
# ✅ CORRECT
from typing import Protocol

class SpeechBackend(Protocol):
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        ...

class WhisperBackend:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        # Implementation
        pass

class MockBackend:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        return "mock transcript"
```

---

## ADR-005: Dataclass for Data Structures

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Need to define data structures (Job, DownloadResult, etc.). Some suggested regular classes with __init__.

### Decision
Use dataclasses for all data structures. Use frozen=True for immutable data.

### Rationale
**Pros**:
- Automatic __init__, __repr__, __eq__
- Less boilerplate code
- Type hints built-in
- Support for immutability (frozen=True)
- Built-in serialization methods

**Cons**:
- Less flexibility than regular classes
- Can't add computed properties easily

**Alternatives Considered**:
1. **Regular classes**: Rejected due to boilerplate
2. **NamedTuple**: Rejected due to immutability limitations
3. **Pydantic**: Rejected due to additional dependency

### Consequences
- All data structures use dataclasses
- Immutable data uses frozen=True
- Business logic NOT in dataclasses
- Clear separation between data and behavior

### Implementation
```python
# ✅ CORRECT
from dataclasses import dataclass

@dataclass(frozen=True)
class DownloadResult:
    success: bool
    video_path: Optional[Path]
    title: Optional[str]
    duration: float

# ❌ AVOID
class DownloadResult:
    def __init__(self, success, video_path, title, duration):
        self.success = success
        self.video_path = video_path
        # ... more boilerplate
```

---

## ADR-006: Service Layer Pattern for Business Logic

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Business logic needs to be organized. Some suggested putting logic in bot handlers or directly in dataclasses.

### Decision
All business logic must live in service layer. Bot handlers are thin adapters only.

### Rationale
**Pros**:
- Clear separation of concerns
- Business logic reusable across interfaces (bot, API, CLI)
- Easy to test business logic independently
- Consistent error handling
- Single responsibility principle

**Cons**:
- Additional layer
- More files to maintain

**Alternatives Considered**:
1. **Logic in bot handlers**: Rejected due to coupling to Telegram
2. **Logic in dataclasses**: Rejected due to mixing data/behavior
3. **Logic in orchestrator only**: Rejected due to god object risk

### Consequences
- Services contain all business logic
- Bot/API/CLI are thin adapters
- Services are stateless (except injected dependencies)
- Services delegate to backends via protocols

### Implementation
```python
# ✅ CORRECT
class TelegramBot:
    def _handle_download(self, chat_id: int, args: List[str]) -> None:
        # Thin adapter: validate, extract args, call service
        url = args[0]
        result = self.download_service.download(url)
        self.adapter.send_message(chat_id, f"Downloaded: {result.video_path}")

# ❌ AVOID
class TelegramBot:
    def _handle_download(self, chat_id: int, args: List[str]) -> None:
        # Business logic in handler (FORBIDDEN)
        url = args[0]
        if not url.startswith("http"):
            raise ValueError("Invalid URL")
        # ... 50 lines of download logic
```

---

## ADR-007: Logging Instead of Print Statements

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Need to output information during execution. Some suggested using print() for simplicity.

### Decision
Use logging module exclusively. No print() statements in production code.

### Rationale
**Pros**:
- Configurable log levels (DEBUG, INFO, WARNING, ERROR)
- Can redirect to files, syslog, etc.
- Structured logging with context
- Can disable in production without code changes
- Thread-safe

**Cons**:
- Slightly more verbose than print()
- Requires configuration

**Alternatives Considered**:
1. **print() statements**: Rejected due to lack of control
2. **Custom logging**: Rejected due to reinventing wheel

### Consequences
- All output uses logging
- Log levels used appropriately
- No print() in production code
- Logging configured per environment

### Implementation
```python
# ✅ CORRECT
import logging

logger = logging.getLogger(__name__)

def process(data):
    logger.debug("Processing data: %s", data)
    result = do_work(data)
    logger.info("Processing complete")
    return result

# ❌ FORBIDDEN
def process(data):
    print(f"Processing data: {data}")
    result = do_work(data)
    print("Processing complete")
    return result
```

---

## ADR-008: Type Hints Required (100% Coverage)

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Python is dynamically typed. Some team members suggested optional type hints.

### Decision
Type hints required for ALL functions and methods. 100% coverage enforced via mypy.

### Rationale
**Pros**:
- Catch errors at type-check time
- Better IDE autocomplete
- Self-documenting code
- Refactoring confidence
- Required for protocol compliance

**Cons**:
- Additional typing overhead
- Some complex types are hard to express

**Alternatives Considered**:
1. **No type hints**: Rejected due to error-prone code
2. **Partial type hints**: Rejected due to inconsistency

### Consequences
- All functions have type hints
- mypy must pass before commit
- Complex types use typing module
- Type: ignore requires comment justification

### Implementation
```python
# ✅ CORRECT
from typing import Optional, List

def process_items(
    items: List[str],
    threshold: Optional[int] = None
) -> List[str]:
    threshold = threshold or 0
    return [item for item in items if len(item) > threshold]

# ❌ FORBIDDEN
def process_items(items, threshold=None):
    threshold = threshold or 0
    return [item for item in items if len(item) > threshold]
```

---

## ADR-009: Composition Over Inheritance

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Need to combine functionality (e.g., audio extraction + transcription). Some suggested multiple inheritance.

### Decision
Use composition exclusively. No multiple inheritance for combining functionality.

### Rationale
**Pros**:
- Flexible (change components at runtime)
- Avoids diamond problem
- Easier to test (inject mocks)
- Clearer dependency graph
- Follows single responsibility principle

**Cons**:
- More delegation code
- Deeper object graphs

**Alternatives Considered**:
1. **Multiple inheritance**: Rejected due to complexity
2. **Mixins**: Rejected due to implicit dependencies

### Consequences
- Classes compose functionality via injection
- No multiple inheritance
- Clear dependency chains
- Easy to swap components

### Implementation
```python
# ✅ CORRECT
class AudioPipeline:
    def __init__(
        self,
        extractor: AudioExtractor,
        transcriber: Optional[SpeechService]
    ):
        self.extractor = extractor
        self.transcriber = transcriber

    def process(self, video_path: Path):
        audio = self.extractor.extract(video_path)
        if self.transcriber:
            text = self.transcriber.transcribe(audio.audio_path)
        return AudioPipelineResult(audio, text)

# ❌ AVOID
class AudioPipeline(AudioExtractor, SpeechService):
    pass  # Multiple inheritance forbidden
```

---

## ADR-010: Immutable Configuration Objects

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Configuration needs to be passed to services. Some suggested mutable config objects.

### Decision
All configuration objects must be immutable (frozen dataclasses). No runtime modification.

### Rationale
**Pros**:
- Thread-safe (no concurrent modification)
- Predictable behavior
- Easy to reason about
- Can be safely shared
- Caching safe

**Cons**:
- Need to create new objects for changes
- Slightly more memory

**Alternatives Considered**:
1. **Mutable config**: Rejected due to race conditions
2. **Global config dict**: Rejected due to hidden state

### Consequences
- All config classes use frozen=True
- Configuration set at startup
- No runtime config changes
- Config objects passed by value

### Implementation
```python
# ✅ CORRECT
@dataclass(frozen=True)
class WhisperConfig:
    model: str = "base"
    device: str = "cpu"
    task: str = "transcribe"

# ❌ FORBIDDEN
class WhisperConfig:
    def __init__(self, model="base", device="cpu"):
        self.model = model
        self.device = device
        # Mutable - can be changed at runtime
```

---

## ADR-011: Exception Hierarchy with Domain Errors

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Need to handle errors. Some suggested using generic Exception or raising strings.

### Decision
Define domain-specific exception hierarchy. Convert external errors to domain errors.

### Rationale
**Pros**:
- Precise error handling
- Clear error semantics
- Easy to catch specific errors
- Better error messages
- Separates external from internal errors

**Cons**:
- More exception classes to maintain

**Alternatives Considered**:
1. **Generic Exception**: Rejected due to imprecise handling
2. **Raising strings**: Rejected (not possible in Python)

### Consequences
- Base exception class for domain
- Specific exceptions per module
- External errors converted at boundaries
- Error messages include context

### Implementation
```python
# ✅ CORRECT
class UniversalVideoAIError(Exception):
    """Base exception for all application errors."""
    pass

class DownloadError(UniversalVideoAIError):
    """Raised when download fails."""
    pass

# Convert external errors
try:
    result = external_api.call()
except ExternalAPIError as exc:
    raise DownloadError(f"Download failed: {exc}") from exc
```

---

## ADR-012: Background Jobs via Thread Pool (Not Celery)

**Status**: Accepted
**Date**: 2026-07-05
**Decision Maker**: Tech Lead

### Context
Need to process jobs in background. Some suggested Celery for production-grade task queue.

### Decision
Use Python threading.Thread for initial implementation. Defer Celery/Redis until Milestone 6.

### Rationale
**Pros**:
- No additional dependencies
- Simpler deployment
- Sufficient for initial scale
- Can migrate to Celery later

**Cons**:
- Not distributed (single process)
- No persistence across restarts
- Limited scalability

**Alternatives Considered**:
1. **Celery + Redis**: Deferred to Milestone 6 (Job Queue System)
2. **asyncio**: Rejected due to complexity with blocking I/O

### Consequences
- Initial implementation uses threading
- JobService manages background threads
- Jobs lost on process restart
- Migration path to Celery defined

### Implementation
```python
# ✅ CORRECT (Milestone 1-5)
from threading import Thread

def run_job_async(self, job_id: str, callback):
    def _worker():
        try:
            result = callback(job_id)
            self.update_job(job_id, status=JobStatus.COMPLETED)
        except Exception as exc:
            self.update_job(job_id, status=JobStatus.FAILED, error=str(exc))

    thread = Thread(target=_worker, daemon=True)
    thread.start()
    return thread

# ✅ CORRECT (Milestone 6+)
# Migrate to Celery + Redis
```

---

## Future Decisions

The following decisions will be recorded when reached:
- ADR-013: Queue System (Celery vs RQ vs custom)
- ADR-014: Monitoring (Prometheus vs CloudWatch vs custom)
- ADR-015: Database (SQLite vs PostgreSQL vs MySQL)
- ADR-016: Caching (Redis vs Memcached vs in-memory)
- ADR-017: Deployment (Docker Compose vs Kubernetes vs Nomad)

## Decision Template

To add a new decision, use this template:

```markdown
## ADR-XXX: [Decision Title]

**Status**: [Proposed | Accepted | Rejected | Deprecated]
**Date**: [YYYY-MM-DD]
**Decision Maker**: [Name/Role]

### Context
[What is the issue we're facing?]

### Decision
[What did we decide?]

### Rationale
[Why did we make this decision? Include pros/cons and alternatives]

### Consequences
[What does this mean for the codebase?]

### Implementation
[Code example showing the decision]
```
