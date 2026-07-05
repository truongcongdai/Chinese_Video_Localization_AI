# Universal Video AI - Import Rules

## Purpose
Defines allowed and forbidden import dependencies between modules. AI MUST follow these rules to prevent architectural violations.

## Dependency Hierarchy (Top to Bottom)

```
Layer 1: Adapters (External Integration)
├── bot/
├── api/
└── webhook/

Layer 2: Orchestrators (Cross-Service Coordination)
├── orchestrator/
└── jobs/

Layer 3: Services (Business Logic)
├── downloader/
├── audio/
├── speech/
├── translate/
├── tts/
├── mixer/
├── render/
└── timeline/

Layer 4: Backends (External API Implementations)
├── speech/whisper.py
├── translate/translator.py
└── tts/tts.py

Layer 5: Core (Shared Infrastructure)
├── config/
├── exceptions/
├── logger/
├── models/
└── database/
```

## Allowed Imports

### Layer 1 (Adapters) MAY Import From:
- Layer 2 (Orchestrators)
- Layer 3 (Services)
- Layer 5 (Core)

**Examples**:
```python
# ✅ ALLOWED
from universal_video_ai.orchestrator import LocalizationService
from universal_video_ai.downloader import DownloadService
from universal_video_ai.database import DatabaseManager
from universal_video_ai.config import Config
```

**FORBIDDEN**:
```python
# ❌ FORBIDDEN - Direct backend import
from universal_video_ai.speech.whisper import WhisperBackend

# ❌ FORBIDDEN - Must use service layer
from universal_video_ai.translate.translator import GoogleTranslator
```

### Layer 2 (Orchestrators) MAY Import From:
- Layer 3 (Services)
- Layer 5 (Core)

**Examples**:
```python
# ✅ ALLOWED
from universal_video_ai.downloader import DownloadService
from universal_video_ai.speech import SpeechService
from universal_video_ai.translate import TranslateService
from universal_video_ai.config import Config
```

**FORBIDDEN**:
```python
# ❌ FORBIDDEN - Upward dependency
from universal_video_ai.bot import TelegramBot

# ❌ FORBIDDEN - Wrong direction
from universal_video_ai.jobs import JobService
```

### Layer 3 (Services) MAY Import From:
- Layer 4 (Backends - via protocols only)
- Layer 5 (Core)

**Examples**:
```python
# ✅ ALLOWED - Protocol import
from universal_video_ai.speech.backend import SpeechBackend
from universal_video_ai.translate.backend import TranslateBackend
from universal_video_ai.tts.backend import TTSBackend
from universal_video_ai.config import Config
```

**FORBIDDEN**:
```python
# ❌ FORBIDDEN - Concrete implementation
from universal_video_ai.speech.whisper import WhisperBackend

# ❌ FORBIDDEN - Upward dependency
from universal_video_ai.orchestrator import LocalizationService

# ❌ FORBIDDEN - Wrong direction
from universal_video_ai.bot import TelegramBot
```

### Layer 4 (Backends) MAY Import From:
- Layer 5 (Core)
- External libraries (whisper, googletrans, edge-tts)

**Examples**:
```python
# ✅ ALLOWED
from universal_video_ai.config import Config
from universal_video_ai.exceptions import SpeechError
import whisper
import googletrans
```

**FORBIDDEN**:
```python
# ❌ FORBIDDEN - Upward dependency
from universal_video_ai.speech import SpeechService

# ❌ FORBIDDEN - Wrong direction
from universal_video_ai.bot import TelegramBot
```

### Layer 5 (Core) MAY Import From:
- Standard library only
- External infrastructure libraries (pathlib, logging, etc.)

**Examples**:
```python
# ✅ ALLOWED
from pathlib import Path
import logging
from typing import Optional
```

**FORBIDDEN**:
```python
# ❌ FORBIDDEN - Core must be independent
from universal_video_ai.downloader import DownloadService

# ❌ FORBIDDEN - Core must be independent
from universal_video_ai.speech import SpeechService
```

## Module-Specific Import Rules

### bot/ Module
**Allowed**:
```python
from universal_video_ai.orchestrator import LocalizationService
from universal_video_ai.downloader import DownloadService
from universal_video_ai.jobs import JobService
from universal_video_ai.database import DatabaseManager
from universal_video_ai.config import Config
```

**Forbidden**:
```python
from universal_video_ai.speech.whisper import WhisperBackend  # ❌ Bypass service
from universal_video_ai.translate.translator import GoogleTranslator  # ❌ Bypass service
```

### speech/ Module
**Allowed**:
```python
from universal_video_ai.speech.backend import SpeechBackend  # Protocol
from universal_video_ai.config import Config
from universal_video_ai.exceptions import SpeechError
```

**Forbidden**:
```python
from universal_video_ai.translate import TranslateService  # ❌ Wrong direction
from universal_video_ai.bot import TelegramBot  # ❌ Wrong direction
```

### translate/ Module
**Allowed**:
```python
from universal_video_ai.translate.backend import TranslateBackend  # Protocol
from universal_video_ai.config import Config
from universal_video_ai.exceptions import TranslationError
```

**Forbidden**:
```python
from universal_video_ai.speech import SpeechService  # ❌ Wrong direction
from universal_video_ai.tts import TTSService  # ❌ Wrong direction
```

### tts/ Module
**Allowed**:
```python
from universal_video_ai.tts.backend import TTSBackend  # Protocol
from universal_video_ai.config import Config
from universal_video_ai.exceptions import TTSError
```

**Forbidden**:
```python
from universal_video_ai.speech import SpeechService  # ❌ Wrong direction
from universal_video_ai.translate import TranslateService  # ❌ Wrong direction
```

### downloader/ Module
**Allowed**:
```python
from universal_video_ai.config import Config
from universal_video_ai.exceptions import DownloadError
```

**Forbidden**:
```python
from universal_video_ai.bot import TelegramBot  # ❌ Wrong direction
from universal_video_ai.speech import SpeechService  # ❌ Wrong direction
```

## Common Violation Patterns

### ❌ Pattern 1: Bypass Service Layer
```python
# FORBIDDEN
from universal_video_ai.speech.whisper import WhisperBackend

# CORRECT
from universal_video_ai.speech import SpeechService
```

### ❌ Pattern 2: Upward Dependency
```python
# FORBIDDEN
from universal_video_ai.bot import TelegramBot

# CORRECT - Use dependency injection
class DownloadService:
    def __init__(self):
        # No upward dependencies
```

### ❌ Pattern 3: Circular Dependency
```python
# FORBIDDEN
# orchestrator/ depends on jobs/
from universal_video_ai.jobs import JobService

# jobs/ depends on orchestrator/
from universal_video_ai.orchestrator import LocalizationService

# CORRECT - Use callback pattern
class JobService:
    def run_job_async(self, job_id: str, callback: Callable):
        thread = Thread(target=callback, args=(job_id,))
        thread.start()
```

### ❌ Pattern 4: Concrete Implementation Dependency
```python
# FORBIDDEN
from universal_video_ai.speech.whisper import WhisperBackend

# CORRECT
from universal_video_ai.speech.backend import SpeechBackend
```

## Enforcement

These import rules are enforced through:
1. Code review (check for violations)
2. AI development guidelines (09_AI_RULES.md)
3. Dependency graph (05_DEPENDENCY_GRAPH.md)
4. Module map (06_MODULE_MAP.md)

Violations will result in:
- Code review rejection
- Requirement to refactor
- Architecture review for pattern changes

## AI Behavior

When AI encounters import violations:
1. Identify the violation
2. Reference this document (IMPORT_RULES.md)
3. Reference the dependency graph (05_DEPENDENCY_GRAPH.md)
4. Propose correct import pattern
5. Explain why current import is forbidden

## Example AI Response

**User**: "Add speech transcription to TelegramBot"

**AI Response**:
```
❌ FORBIDDEN: Direct import of WhisperBackend

Current code attempts:
from universal_video_ai.speech.whisper import WhisperBackend

This violates IMPORT_RULES.md:
- Layer 1 (bot/) cannot import Layer 4 (backends)
- Must use service layer (SpeechService)

Correct approach:
from universal_video_ai.speech import SpeechService

class TelegramBot:
    def __init__(self, speech_service: SpeechService):
        self.speech_service = speech_service

    def _handle_transcribe(self, audio_path: Path):
        text = self.speech_service.transcribe(audio_path)
```

These import rules ensure clean architecture and prevent AI from making common architectural mistakes.
