# Universal Video AI - Coding Constitution

## Purpose
This document defines the coding standards and rules that ALL developers (human and AI) MUST follow. Violations will be rejected in code review.

## NEVER (Forbidden Patterns)

### 1. NO print() Statements
```python
# ❌ FORBIDDEN
print("Processing video...")

# ✅ REQUIRED
logger.info("Processing video...")
```

**Reason**: print() cannot be configured, filtered, or redirected in production. Use logging.

### 2. NO os.path
```python
# ❌ FORBIDDEN
import os
path = os.path.join("/tmp", "file.txt")

# ✅ REQUIRED
from pathlib import Path
path = Path("/tmp") / "file.txt"
```

**Reason**: os.path is string-based, error-prone, and not type-safe. Pathlib is modern, type-safe, and cross-platform.

### 3. NO Global Variables
```python
# ❌ FORBIDDEN
DATABASE = None

def init_db():
    global DATABASE
    DATABASE = SQLite()

# ✅ REQUIRED
class Service:
    def __init__(self, database: Database):
        self.database = database
```

**Reason**: Global state makes testing impossible and causes race conditions. Use dependency injection.

### 4. NO staticmethod (Almost Never)
```python
# ❌ FORBIDDEN
class Utils:
    @staticmethod
    def process(data):
        return data * 2

# ✅ REQUIRED
def process(data):
    return data * 2

# OR if it belongs to a class:
class Processor:
    def process(self, data):
        return data * 2
```

**Reason**: staticmethod is rarely needed. Use module-level functions or instance methods.

### 5. NO Business Logic in __main__
```python
# ❌ FORBIDDEN
if __name__ == "__main__":
    # 50 lines of business logic here
    url = sys.argv[1]
    download(url)
    process(url)

# ✅ REQUIRED
def main():
    args = parse_args()
    service = create_service()
    service.process(args.url)

if __name__ == "__main__":
    main()
```

**Reason**: Business logic in __main__ cannot be tested or imported. Extract to functions.

### 6. NO Bare Except
```python
# ❌ FORBIDDEN
try:
    risky_operation()
except:
    pass

# ✅ REQUIRED
try:
    risky_operation()
except SpecificError as exc:
    logger.error("Operation failed: %s", exc)
```

**Reason**: Bare except catches system signals (KeyboardInterrupt) and hides bugs.

### 7. NO Mutable Default Arguments
```python
# ❌ FORBIDDEN
def process(items=[]):
    items.append("item")
    return items

# ✅ REQUIRED
def process(items: Optional[List] = None):
    items = items or []
    items.append("item")
    return items
```

**Reason**: Mutable defaults are shared across all calls, causing subtle bugs.

### 8. NO Type: ignore (Without Comment)
```python
# ❌ FORBIDDEN
result = some_function()  # type: ignore

# ✅ REQUIRED
result = some_function()  # type: ignore[assignment] # TODO: fix type hint
```

**Reason**: Unexplained type ignores hide real type errors. Must justify each one.

### 9. NO Hardcoded Paths
```python
# ❌ FORBIDDEN
output_dir = "/tmp/output"

# ✅ REQUIRED
from universal_video_ai.config import OUTPUT_DIR
output_dir = OUTPUT_DIR
```

**Reason**: Hardcoded paths break on different systems and environments.

### 10. NO Direct Exception Catching of Base Exception
```python
# ❌ FORBIDDEN
try:
    operation()
except Exception:
    pass

# ✅ REQUIRED
try:
    operation()
except (ValueError, KeyError) as exc:
    logger.warning("Expected error: %s", exc)
```

**Reason**: Catching Exception hides system errors and bugs.

## ALWAYS (Required Patterns)

### 1. ALWAYS Use Pathlib
```python
# ✅ REQUIRED
from pathlib import Path

def process_file(path: Path) -> Path:
    output = path.parent / "output" / path.name
    output.parent.mkdir(parents=True, exist_ok=True)
    return output
```

### 2. ALWAYS Use Logging
```python
# ✅ REQUIRED
import logging

logger = logging.getLogger(__name__)

def process(data):
    logger.debug("Starting process with data: %s", data)
    try:
        result = do_work(data)
        logger.info("Process completed successfully")
        return result
    except Exception as exc:
        logger.exception("Process failed: %s", exc)
        raise
```

### 3. ALWAYS Use Dataclasses for Data
```python
# ✅ REQUIRED
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Result:
    success: bool
    data: Optional[str]
    error: Optional[str]
```

### 4. ALWAYS Use Protocols for Interfaces
```python
# ✅ REQUIRED
from typing import Protocol

class Processor(Protocol):
    def process(self, data: str) -> str:
        ...

class RealProcessor:
    def process(self, data: str) -> str:
        return data.upper()
```

### 5. ALWAYS Use Dependency Injection
```python
# ✅ REQUIRED
class Service:
    def __init__(self, database: Database, logger: logging.Logger):
        self.database = database
        self.logger = logger

# Factory
def create_service() -> Service:
    return Service(
        database=SQLite(),
        logger=logging.getLogger(__name__)
    )
```

### 6. ALWAYS Use Composition Over Inheritance
```python
# ✅ REQUIRED
class AudioProcessor:
    def __init__(self, extractor: AudioExtractor, transcriber: Transcriber):
        self.extractor = extractor
        self.transcriber = transcriber

# ❌ AVOID
class AudioProcessor(AudioExtractor, Transcriber):
    pass
```

### 7. ALWAYS Use Service Layer Pattern
```python
# ✅ REQUIRED
class SpeechService:
    def __init__(self, backend: SpeechBackend):
        self.backend = backend

    def transcribe(self, audio_path: Path) -> str:
        if not audio_path.exists():
            raise ValueError(f"Audio file not found: {audio_path}")
        return self.backend.transcribe(audio_path)
```

### 8. ALWAYS Type Hint Everything
```python
# ✅ REQUIRED
from typing import Optional, List

def process_items(
    items: List[str],
    threshold: Optional[int] = None
) -> List[str]:
    threshold = threshold or 0
    return [item for item in items if len(item) > threshold]
```

### 9. ALWAYS Use Context Managers for Resources
```python
# ✅ REQUIRED
def read_file(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()

# ✅ REQUIRED for temporary files
import tempfile

def process_temp():
    with tempfile.NamedTemporaryFile() as tmp:
        # use tmp
        pass
```

### 10. ALWAYS Handle Specific Exceptions
```python
# ✅ REQUIRED
from universal_video_ai.exceptions import TranslationError

def translate(text: str) -> str:
    try:
        return backend.translate(text)
    except TranslationError as exc:
        logger.error("Translation failed: %s", exc)
        raise
```

## Code Style

### Formatting
- Use Black formatter (auto-format on save)
- Line length: 88 characters (Black default)
- Use double quotes for strings
- Use f-strings for formatting

### Imports
- Standard library first
- Third-party libraries second
- Local imports third
- Sort imports alphabetically
- Use `from __future__ import annotations` at top

### Naming
- Classes: PascalCase (e.g., `DownloadService`)
- Functions/variables: snake_case (e.g., `download_video`)
- Constants: UPPER_SNAKE_CASE (e.g., `MAX_RETRIES`)
- Private: _leading_underscore (e.g., `_internal_method`)
- Protocols: End with "Protocol" or clear name (e.g., `SpeechBackend`)

### Documentation
- Docstrings for all public classes and functions
- Use Google-style docstrings
- Include type hints in docstrings
- Document exceptions raised
- Document return values

## Testing Rules

### 1. Test Structure
```python
# ✅ REQUIRED
def test_function_name_scenario_expected_result():
    # Arrange
    input_data = create_test_data()

    # Act
    result = function_under_test(input_data)

    # Assert
    assert result == expected_value
```

### 2. Mock External Dependencies
```python
# ✅ REQUIRED
from unittest.mock import Mock, patch

def test_download_with_mock():
    mock_downloader = Mock()
    mock_downloader.download.return_value = DownloadResult(success=True)

    service = DownloadService(downloader=mock_downloader)
    result = service.download("http://example.com")

    assert result.success
    mock_downloader.download.assert_called_once()
```

### 3. Test Edge Cases
```python
# ✅ REQUIRED
def test_process_empty_input():
    result = process("")
    assert result == ""

def test_process_none_input():
    with pytest.raises(ValueError):
        process(None)
```

### 4. Use Fixtures for Common Setup
```python
# ✅ REQUIRED
@pytest.fixture
def sample_audio():
    return Path("tests/fixtures/sample.wav")

def test_transcribe(sample_audio):
    result = transcriber.transcribe(sample_audio)
    assert len(result) > 0
```

## Performance Rules

### 1. Lazy Loading
```python
# ✅ REQUIRED
class Service:
    def __init__(self):
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = load_heavy_model()
        return self._model
```

### 2. Use Generators for Large Data
```python
# ✅ REQUIRED
def process_large_file(path: Path):
    with path.open() as f:
        for line in f:
            yield process_line(line)

# ❌ AVOID
def process_large_file(path: Path):
    with path.open() as f:
        lines = f.readlines()  # Loads entire file
        return [process_line(line) for line in lines]
```

### 3. Cache Expensive Operations
```python
# ✅ REQUIRED
from functools import lru_cache

@lru_cache(maxsize=128)
def expensive_computation(x: int, y: int) -> int:
    return x ** y
```

## Security Rules

### 1. Never Log Secrets
```python
# ❌ FORBIDDEN
logger.info("API key: %s", api_key)

# ✅ REQUIRED
logger.info("Using API key: %s", mask_secret(api_key))

def mask_secret(key: str) -> str:
    return key[:4] + "*" * (len(key) - 8) + key[-4:]
```

### 2. Validate Input Early
```python
# ✅ REQUIRED
def process_url(url: str) -> DownloadResult:
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    # ... rest of function
```

### 3. Use Parameterized Queries
```python
# ✅ REQUIRED
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

# ❌ FORBIDDEN
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
```

## Error Handling Rules

### 1. Convert External Errors to Domain Errors
```python
# ✅ REQUIRED
try:
    result = external_api.call()
except ExternalAPIError as exc:
    raise TranslationError(f"Translation failed: {exc}") from exc
```

### 2. Provide Context in Errors
```python
# ✅ REQUIRED
raise ValueError(f"Invalid URL: {url}. Must start with http:// or https://")

# ❌ AVOID
raise ValueError("Invalid URL")
```

### 3. Use Custom Exception Hierarchy
```python
# ✅ REQUIRED
class UniversalVideoAIError(Exception):
    """Base exception for all application errors."""
    pass

class DownloadError(UniversalVideoAIError):
    """Raised when download fails."""
    pass
```

## Review Checklist

Before submitting code, verify:
- [ ] No print() statements
- [ ] No os.path usage
- [ ] No global variables
- [ ] No staticmethod (unless justified)
- [ ] All functions have type hints
- [ ] All public functions have docstrings
- [ ] All exceptions are specific
- [ ] All file operations use pathlib
- [ ] All logging uses logger, not print
- [ ] All external dependencies are injected
- [ ] Tests added for new functionality
- [ ] Black formatting applied
- [ ] mypy type checking passes
- [ ] ruff linting passes

## Enforcement

These rules are enforced through:
1. Automated pre-commit hooks (black, ruff, mypy)
2. Code review requirements
3. CI/CD pipeline checks
4. AI development guidelines (see 09_AI_RULES.md)

Violations will result in:
- Code review rejection
- CI/CD failure
- Requirement to fix before merge

This constitution ensures code quality, maintainability, and consistency across all contributors (human and AI).
