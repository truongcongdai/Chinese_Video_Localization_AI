# Universal Video AI - Public API Contracts

## Purpose
This document defines the public API contracts for Universal Video AI. These APIs are guaranteed to be stable within major versions. Breaking changes require major version bump.

## API Stability Guarantees

### Stable APIs
These APIs are guaranteed stable within the current major version:
- Service class public methods
- Protocol definitions
- Dataclass public fields
- Factory function signatures

### Internal APIs
These are considered internal and may change:
- Private methods (prefixed with _)
- Module-level functions not in __all__
- Implementation details
- Helper classes

## Service Layer APIs

### DownloadService

**Location**: `src/universal_video_ai/downloader/service.py`

**Public Methods**:
```python
class DownloadService:
    def download(self, url: str, output_dir: Path) -> DownloadResult:
        """Download video from URL to output directory.
        
        Args:
            url: Video URL to download
            output_dir: Directory to save video
            
        Returns:
            DownloadResult with success status and video path
            
        Raises:
            ValueError: If URL is invalid
            DownloadError: If download fails
        """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### SpeechService

**Location**: `src/universal_video_ai/speech/service.py`

**Public Methods**:
```python
class SpeechService:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Transcribe audio file to text.
        
        Args:
            audio_path: Path to audio file
            language: Language code (e.g., 'zh', 'en')
            
        Returns:
            Transcribed text
            
        Raises:
            ValueError: If audio file not found
            SpeechServiceError: If no backend configured
            TranscriptionError: If transcription fails
        """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### TranslateService

**Location**: `src/universal_video_ai/translate/service.py`

**Public Methods**:
```python
class TranslateService:
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text from source to target language.
        
        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            
        Returns:
            Translated text
            
        Raises:
            ValueError: If text is empty
            TranslationBackendUnavailable: If no backend configured
            TranslationFailed: If translation fails
        """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### TTSService

**Location**: `src/universal_video_ai/tts/service.py`

**Public Methods**:
```python
class TTSService:
    def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str = "en",
        voice: Optional[str] = None
    ) -> Path:
        """Synthesize text to speech audio file.
        
        Args:
            text: Text to synthesize
            output_path: Path to save audio file
            language: Language code (e.g., 'en', 'vi')
            voice: Voice identifier (provider-specific)
            
        Returns:
            Path to generated audio file
            
        Raises:
            ValueError: If text is empty
            TTSBackendUnavailable: If no backend configured
            SynthesisError: If synthesis fails
        """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### LocalizationService

**Location**: `src/universal_video_ai/orchestrator/service.py`

**Public Methods**:
```python
class LocalizationService:
    def localize(self, url: str, output_dir: Path) -> LocalizationResult:
        """Execute full video localization workflow.
        
        Args:
            url: Video URL to process
            output_dir: Directory to save all artifacts
            
        Returns:
            LocalizationResult with all pipeline outputs
            
        Raises:
            ValueError: If download fails
            LocalizationError: If pipeline fails
        """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### JobService

**Location**: `src/universal_video_ai/jobs/service.py`

**Public Methods**:
```python
class JobService:
    def create_job(self, config: JobConfig) -> Job:
        """Create a new job record.
        
        Args:
            config: Job configuration
            
        Returns:
            Created Job instance
        """
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve job by ID.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Job instance or None if not found
        """
    
    def list_jobs(self, status: Optional[JobStatus] = None) -> List[Job]:
        """List all jobs, optionally filtered by status.
        
        Args:
            status: Optional filter by job status
            
        Returns:
            List of Job instances
        """
    
    def update_job(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        progress: Optional[float] = None,
        message: str = "",
        error: Optional[str] = None,
        result_path: Optional[Path] = None
    ) -> Job:
        """Update job status and progress.
        
        Args:
            job_id: Job identifier
            status: New job status
            progress: Progress (0.0 to 1.0)
            message: Status message
            error: Error message if failed
            result_path: Path to result file
            
        Returns:
            Updated Job instance
            
        Raises:
            ValueError: If job not found
        """
    
    def run_job_async(self, job_id: str, callback: Callable) -> Thread:
        """Run a job in a background thread.
        
        Args:
            job_id: Job identifier
            callback: Function to execute
            
        Returns:
            Started Thread instance
            
        Raises:
            ValueError: If job not found
        """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### TelegramBot

**Location**: `src/universal_video_ai/bot/telegram_bot.py`

**Public Methods**:
```python
class TelegramBot:
    def start(self) -> None:
        """Start bot adapter polling/event loop."""
    
    def stop(self) -> None:
        """Stop bot adapter polling/event loop."""
```

**Constructor**:
```python
class TelegramBot:
    def __init__(
        self,
        adapter: TelegramAdapter,
        download_service: DownloadService,
        localization_service: Optional[LocalizationService] = None,
        database_manager: Optional[DatabaseManager] = None,
        admin_chat_ids: Optional[Set[int]] = None,
        output_dir: Path | str = TEMP_DIR,
        validator: UrlValidator | None = None,
        logger: Optional[logging.Logger] = None
    ) -> None:
```

**Stability**: Stable (v1.x)

**Breaking Changes**: Constructor parameters may be added with defaults

---

## Protocol APIs

### SpeechBackend

**Location**: `src/universal_video_ai/speech/backend.py`

**Protocol Definition**:
```python
class SpeechBackend(Protocol):
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Return transcript text for the given audio file."""
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### TranslateBackend

**Location**: `src/universal_video_ai/translate/backend.py`

**Protocol Definition**:
```python
class TranslateBackend(Protocol):
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text from source to target language."""
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### TTSBackend

**Location**: `src/universal_video_ai/tts/backend.py`

**Protocol Definition**:
```python
class TTSBackend(Protocol):
    def synthesize(self, text: str, output_path: Path, language: str = "en") -> Path:
        """Synthesize speech to audio file."""
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### TTS

**Location**: `src/universal_video_ai/tts/tts.py`

**Protocol Definition**:
```python
class TTS(Protocol):
    def synthesize(self, text: str, output_path: Path) -> Path:
        """Synthesize `text` to a media file at `output_path`."""
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

### Translator

**Location**: `src/universal_video_ai/translate/translator.py`

**Protocol Definition**:
```python
class Translator(Protocol):
    def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
        """Translate `text` from src_lang to dest_lang."""
```

**Stability**: Stable (v1.x)

**Breaking Changes**: None allowed in v1.x

---

## Data Model APIs

### DownloadResult

**Location**: `src/universal_video_ai/downloader/download_result.py`

**Dataclass Fields**:
```python
@dataclass(frozen=True)
class DownloadResult:
    success: bool
    video_path: Optional[Path]
    title: Optional[str]
    duration: float
    platform: str
```

**Stability**: Stable (v1.x)

**Breaking Changes**: Fields may be added (with defaults), not removed

---

### Job

**Location**: `src/universal_video_ai/jobs/models.py`

**Dataclass Fields**:
```python
@dataclass
class Job:
    job_id: str
    config: JobConfig
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    message: str = ""
    result_path: Optional[Path] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0
```

**Stability**: Stable (v1.x)

**Breaking Changes**: Fields may be added (with defaults), not removed

---

### JobConfig

**Location**: `src/universal_video_ai/jobs/models.py`

**Dataclass Fields**:
```python
@dataclass(frozen=True)
class JobConfig:
    url: str
    output_dir: Path
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    run_translation: bool = False
    target_language: Optional[str] = None
    run_tts: bool = False
    run_demucs: bool = False
    generate_subtitles: bool = False
    mix_audio: bool = False
```

**Stability**: Stable (v1.x)

**Breaking Changes**: Fields may be added (with defaults), not removed

---

### LocalizationResult

**Location**: `src/universal_video_ai/orchestrator/service.py`

**Dataclass Fields**:
```python
@dataclass(frozen=True)
class LocalizationResult:
    download_result: DownloadResult
    audio_pipeline_result: AudioPipelineResult
    translated_text: Optional[str] = None
    tts_audio_path: Optional[Path] = None
    subtitle_segments: Optional[object] = None
    mixed_audio_path: Optional[Path] = None
    final_video_path: Optional[Path] = None
```

**Stability**: Stable (v1.x)

**Breaking Changes**: Fields may be added (with defaults), not removed

---

## Factory APIs

### create_localization_service

**Location**: `src/universal_video_ai/orchestrator/factory.py`

**Function Signature**:
```python
def create_localization_service(
    run_demucs: bool = False,
    run_transcription: bool = False,
    transcription_language: Optional[str] = None,
    run_translation: bool = False,
    target_language: Optional[str] = None,
    run_tts: bool = False,
    generate_subtitles: bool = False,
    mix_audio: bool = False,
    logger: Optional[logging.Logger] = None
) -> LocalizationService:
    """Factory function to create LocalizationService with configured backends.
    
    Args:
        run_demucs: Enable audio separation
        run_transcription: Enable speech transcription
        transcription_language: Transcription language code
        run_translation: Enable translation
        target_language: Target language for translation
        run_tts: Enable text-to-speech
        generate_subtitles: Enable subtitle generation
        mix_audio: Enable audio mixing
        logger: Logger instance
        
    Returns:
        Configured LocalizationService instance
    """
```

**Stability**: Stable (v1.x)

**Breaking Changes**: Parameters may be added (with defaults), not removed

---

## Exception Hierarchy

### Base Exception

**Location**: `src/universal_video_ai/exceptions.py`

```python
class UniversalVideoAIError(Exception):
    """Base exception for all application errors."""
```

**Stability**: Stable (v1.x)

---

### Module-Specific Exceptions

**Speech**:
- `SpeechError` - Base speech errors
- `SpeechServiceError` - Service misconfiguration
- `SpeechBackendUnavailable` - No backend configured
- `TranscriptionError` - Transcription failure

**Translation**:
- `TranslateError` - Base translation errors
- `TranslateServiceError` - Service misconfiguration
- `TranslationBackendUnavailable` - No backend configured
- `TranslationFailed` - Translation failure

**TTS**:
- `TTSError` - Base TTS errors
- `TTSServiceError` - Service misconfiguration
- `TTSBackendUnavailable` - No backend configured
- `SynthesisError` - Synthesis failure

**Stability**: Stable (v1.x)

**Breaking Changes**: New exception types may be added, existing ones not removed

---

## API Usage Examples

### Download Video
```python
from universal_video_ai.downloader.service import DownloadService
from pathlib import Path

downloader = DownloadService()
result = downloader.download(
    url="https://example.com/video.mp4",
    output_dir=Path("/tmp/videos")
)

if result.success:
    print(f"Downloaded: {result.video_path}")
```

### Transcribe Audio
```python
from universal_video_ai.speech.service import SpeechService
from universal_video_ai.speech.backend import WhisperBackend
from pathlib import Path

backend = WhisperBackend()
service = SpeechService(backend=backend)
text = service.transcribe(
    audio_path=Path("/tmp/audio.wav"),
    language="zh"
)
print(f"Transcript: {text}")
```

### Translate Text
```python
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.translate.backend import TranslatorBackend

service = TranslateService(backend=TranslatorBackend())
translated = service.translate(
    text="Hello world",
    source_lang="en",
    target_lang="vi"
)
print(f"Translated: {translated}")
```

### Full Localization
```python
from universal_video_ai.orchestrator.factory import create_localization_service
from pathlib import Path

service = create_localization_service(
    run_transcription=True,
    transcription_language="zh",
    run_translation=True,
    target_language="vi",
    run_tts=True,
    generate_subtitles=True,
    mix_audio=True
)

result = service.localize(
    url="https://example.com/video.mp4",
    output_dir=Path("/tmp/output")
)

if result.final_video_path:
    print(f"Localized video: {result.final_video_path}")
```

---

## Versioning Policy

### Semantic Versioning
- **Major**: Breaking changes to public APIs
- **Minor**: New features, backward-compatible
- **Patch**: Bug fixes, backward-compatible

### Deprecation Process
1. Mark as deprecated in documentation
2. Add deprecation warning in code
3. Maintain for at least one minor version
4. Remove in next major version

### Breaking Change Criteria
A change is considered breaking if:
- Public method signature changes
- Public method is removed
- Protocol definition changes
- Dataclass field is removed
- Exception type is removed
- Behavior change breaks existing code

## API Compatibility

### Backward Compatibility
All changes must maintain backward compatibility:
- Add optional parameters with defaults
- Add new methods (don't remove existing)
- Add new dataclass fields (with defaults)
- Extend exceptions (don't remove)

### Migration Guide
When breaking changes are necessary:
1. Document in CHANGELOG
2. Provide migration guide
3. Update examples
4. Announce in release notes

## Enforcement

API contracts are enforced through:
1. Code review (check for violations)
2. Module map (06_MODULE_MAP.md)
3. Architecture decisions (03_DECISIONS.md)
4. Versioning policy
5. Deprecation process

Violations will result in:
- Code review rejection
- Requirement to use alternative approach
- Architectural review for breaking changes

This public API documentation ensures stability and predictability for users of Universal Video AI.
