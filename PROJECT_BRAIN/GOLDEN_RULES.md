# Universal Video AI - Golden Rules

## Purpose
These are the immutable principles that guide all development decisions. AI MUST follow these rules without exception.

## Golden Rule 1: Never Rewrite Working Code
**Principle**: If code works and meets requirements, do not rewrite it.

**Examples**:
- ✅ Keep existing DownloadService if it downloads videos correctly
- ✅ Keep existing AudioPipeline if it extracts audio correctly
- ❌ Do not rewrite SpeechService just to use a different pattern
- ❌ Do not refactor working code for "prettier" syntax

**Exceptions**:
- Security vulnerabilities
- Performance critical issues
- Breaking changes in dependencies
- Explicit architectural decision (ADR)

**AI Behavior**:
Before suggesting rewrite, ask:
1. Does current code work?
2. Does current code meet requirements?
3. Is there a specific problem to solve?
4. Is rewrite the ONLY solution?

## Golden Rule 2: Prefer Extension Over Modification
**Principle**: Add new functionality by extending, not modifying existing code.

**Examples**:
- ✅ Add new downloader for new platform (extend)
- ✅ Add new backend implementation (extend)
- ✅ Add new translation engine (extend)
- ❌ Modify existing downloader to support new platform (modify)
- ❌ Change service signature to add optional parameter (modify)

**Implementation**:
```python
# ✅ CORRECT - Extension
class YouTubeDownloader(DownloaderProtocol):
    def download(self, url: str) -> DownloadResult:
        # YouTube-specific implementation

class TikTokDownloader(DownloaderProtocol):
    def download(self, url: str) -> DownloadResult:
        # TikTok-specific implementation

# ❌ FORBIDDEN - Modification
class DownloaderService:
    def download(self, url: str, platform: str) -> DownloadResult:
        # Modified to support multiple platforms
```

**AI Behavior**:
When adding functionality:
1. Can this be a new implementation?
2. Can this be a new class?
3. Can this be a new module?
4. Only modify if extension is impossible

## Golden Rule 3: Composition Over Inheritance
**Principle**: Use composition (dependency injection) instead of inheritance hierarchies.

**Examples**:
- ✅ Service depends on protocol via DI
- ✅ Backend injected into service
- ✅ Adapter pattern for external APIs
- ❌ Deep inheritance hierarchies
- ❌ Base classes with many subclasses
- ❌ Method overriding for behavior

**Implementation**:
```python
# ✅ CORRECT - Composition
class SpeechService:
    def __init__(self, backend: SpeechBackend):
        self.backend = backend

# ❌ FORBIDDEN - Inheritance
class WhisperSpeechService(BaseSpeechService):
    def transcribe(self):
        # Override base method
```

**AI Behavior**:
When designing:
1. Can I use dependency injection?
2. Can I use protocol-based design?
3. Can I use composition?
4. Only use inheritance if composition is impossible

## Golden Rule 4: One Commit One Concern
**Principle**: Each commit should address a single concern or fix a single issue.

**Examples**:
- ✅ Commit: "Add Whisper backend implementation"
- ✅ Commit: "Fix audio extraction bug"
- ✅ Commit: "Add logging to SpeechService"
- ❌ Commit: "Add Whisper, fix audio, add logging" (multiple concerns)
- ❌ Commit: "Refactor speech and translate modules" (multiple concerns)

**Commit Template**:
```
<type>: <subject>

<body>

- Change 1
- Change 2
```

**AI Behavior**:
When implementing:
1. What is the single concern?
2. Can this be split into multiple commits?
3. Does each commit pass tests independently?
4. Keep commits focused and atomic

## Golden Rule 5: Every Feature Injectable
**Principle**: All features and dependencies should be injectable, not hardcoded.

**Examples**:
- ✅ Backend injected via constructor
- ✅ Config injected via constructor
- ✅ Logger injected via constructor
- ❌ Hardcoded backend instantiation
- ❌ Hardcoded config values
- ❌ Hardcoded file paths

**Implementation**:
```python
# ✅ CORRECT - Injectable
class SpeechService:
    def __init__(
        self,
        backend: SpeechBackend,
        config: Config,
        logger: Logger
    ):
        self.backend = backend
        self.config = config
        self.logger = logger

# ❌ FORBIDDEN - Hardcoded
class SpeechService:
    def __init__(self):
        self.backend = WhisperBackend()  # Hardcoded
        self.config = load_config()  # Hardcoded
        self.logger = setup_logger()  # Hardcoded
```

**AI Behavior**:
When creating classes:
1. What dependencies does this have?
2. Can they be injected?
3. Can they be passed via constructor?
4. Make everything injectable

## Golden Rule 6: Every Backend Replaceable
**Principle**: Backend implementations must be replaceable without changing service code.

**Examples**:
- ✅ WhisperBackend replaceable with GoogleSpeechBackend
- ✅ GoogleTranslator replaceable with DeepLTranslator
- ✅ EdgeTTS replaceable with AzureTTS
- ❌ Service depends on concrete backend
- ❌ Backend-specific code in service
- ❌ Backend-specific imports in service

**Implementation**:
```python
# ✅ CORRECT - Replaceable
class SpeechService:
    def __init__(self, backend: SpeechBackend):
        self.backend = backend

# Can replace with any SpeechBackend implementation
service = SpeechService(WhisperBackend())
service = SpeechService(GoogleSpeechBackend())

# ❌ FORBIDDEN - Not replaceable
class SpeechService:
    def __init__(self):
        self.backend = WhisperBackend()  # Concrete
```

**AI Behavior**:
When implementing backends:
1. Is there a protocol?
2. Does service depend on protocol?
3. Can I swap implementations?
4. Ensure protocol-based design

## Golden Rule 7: Everything Testable
**Principle**: All code must be testable without external dependencies.

**Examples**:
- ✅ Protocols allow mocking
- ✅ Dependency injection allows mocking
- ✅ Pure functions are testable
- ❌ Hardcoded external dependencies
- ❌ Global state
- ❌ Side effects in pure functions

**Implementation**:
```python
# ✅ CORRECT - Testable
class SpeechService:
    def __init__(self, backend: SpeechBackend):
        self.backend = backend

# Test with mock
def test_speech_service():
    mock_backend = Mock(spec=SpeechBackend)
    service = SpeechService(mock_backend)
    # Test service behavior

# ❌ FORBIDDEN - Not testable
class SpeechService:
    def __init__(self):
        self.backend = WhisperBackend()  # Real dependency
```

**AI Behavior**:
When writing code:
1. Can I mock dependencies?
2. Can I test this in isolation?
3. Are external dependencies injectable?
4. Write tests alongside code

## Golden Rule 8: Protocol-Based Design
**Principle**: All cross-module interfaces must use protocols, not concrete classes.

**Examples**:
- ✅ SpeechBackend protocol
- ✅ TranslateBackend protocol
- ✅ TTS protocol
- ❌ Service depends on concrete class
- ❌ Direct instantiation of concrete class
- ❌ Type hints using concrete class

**Implementation**:
```python
# ✅ CORRECT - Protocol-based
from universal_video_ai.speech.backend import SpeechBackend

class SpeechService:
    def __init__(self, backend: SpeechBackend):
        self.backend = backend

# ❌ FORBIDDEN - Concrete-based
from universal_video_ai.speech.whisper import WhisperBackend

class SpeechService:
    def __init__(self, backend: WhisperBackend):
        self.backend = backend
```

**AI Behavior**:
When designing interfaces:
1. Is there a protocol?
2. Can I use protocol in type hint?
3. Can I use protocol in dependency?
4. Always prefer protocols

## Golden Rule 9: Immutable Configuration
**Principle**: Configuration must be immutable after loading.

**Examples**:
- ✅ Frozen dataclass for config
- ✅ Config loaded once at startup
- ✅ No runtime config modification
- ❌ Mutable config objects
- ❌ Runtime config changes
- ❌ Global config state

**Implementation**:
```python
# ✅ CORRECT - Immutable
@dataclass(frozen=True)
class Config:
    download_dir: Path
    max_retries: int

# ❌ FORBIDDEN - Mutable
class Config:
    def __init__(self):
        self.download_dir = Path("/tmp")
        self.max_retries = 3
```

**AI Behavior**:
When handling config:
1. Is config frozen?
2. Can config be modified at runtime?
3. Is config loaded once?
4. Make config immutable

## Golden Rule 10: Explicit Over Implicit
**Principle**: Make all dependencies and behaviors explicit, not implicit.

**Examples**:
- ✅ Explicit constructor parameters
- ✅ Explicit type hints
- ✅ Explicit error handling
- ❌ Implicit dependencies
- ❌ Implicit type conversions
- ❌ Implicit error handling

**Implementation**:
```python
# ✅ CORRECT - Explicit
class SpeechService:
    def __init__(
        self,
        backend: SpeechBackend,
        config: Config,
        logger: Logger
    ):
        self.backend = backend
        self.config = config
        self.logger = logger

# ❌ FORBIDDEN - Implicit
class SpeechService:
    def __init__(self):
        # Implicit dependencies loaded from global state
        self.backend = get_backend()
        self.config = get_config()
        self.logger = get_logger()
```

**AI Behavior**:
When writing code:
1. Are all dependencies explicit?
2. Are all types explicit?
3. Are all behaviors explicit?
4. Make everything explicit

## Enforcement

These golden rules are enforced through:
1. Code review (check for violations)
2. AI development guidelines (09_AI_RULES.md)
3. Module map (06_MODULE_MAP.md)
4. Import rules (IMPORT_RULES.md)

Violations will result in:
- Code review rejection
- Requirement to refactor
- Architecture review for pattern changes

## AI Behavior

When AI encounters golden rule violations:
1. Identify the violation
2. Reference this document (GOLDEN_RULES.md)
3. Reference specific golden rule
4. Propose correct approach
5. Explain why current approach violates rule

## Example AI Response

**User**: "Add Whisper backend directly in SpeechService"

**AI Response**:
```
❌ VIOLATION: Golden Rule 6 (Every Backend Replaceable)

Current code attempts:
class SpeechService:
    def __init__(self):
        self.backend = WhisperBackend()

This violates GOLDEN_RULES.md:
- Golden Rule 6: Backend must be replaceable
- Golden Rule 5: Every feature must be injectable
- Golden Rule 8: Must use protocol-based design

Correct approach:
from universal_video_ai.speech.backend import SpeechBackend

class SpeechService:
    def __init__(self, backend: SpeechBackend):
        self.backend = backend

# Usage allows backend replacement
service = SpeechService(WhisperBackend())
service = SpeechService(GoogleSpeechBackend())
```

These golden rules ensure consistent, maintainable, and testable code across the entire project.
