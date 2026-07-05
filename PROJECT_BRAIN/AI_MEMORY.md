# Universal Video AI - AI Memory

## Purpose
Tracks known facts about the project to prevent AI from reading git history and asking redundant questions. This document is updated as modules are completed.

## KNOWN FACTS

### Module Completion Status

#### downloader/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- DownloadService implemented with protocol-based design
- Supports YouTube and TikTok platforms
- DownloadResult dataclass frozen
- Platform detection implemented
- URL validation implemented
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting DownloadService (Golden Rule 1)
- Can add new platform downloaders via extension (Golden Rule 2)
- Do not modify DownloadResult structure (frozen)

---

#### audio/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- AudioPipeline implemented
- Audio extraction from video working
- Demucs integration for vocal separation
- AudioResult dataclass frozen
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting AudioPipeline (Golden Rule 1)
- Can add new audio processors via extension (Golden Rule 2)
- Do not modify AudioResult structure (frozen)

---

#### speech/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- SpeechBackend protocol defined
- SpeechService implemented with caching support
- WhisperBackend implemented
- TranscriptionResult dataclass frozen
- Caching support added via RedisCache
- Error handling improved
- Tests passing
- No known issues

**AI Implications**:
- SpeechBackend protocol is frozen (do not modify)
- Caching added via extension (Golden Rule 2)
- Do not modify TranscriptionResult structure (frozen)
- Can add more speech engines via extension

---

#### translate/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- TranslateBackend protocol defined
- TranslateService implemented with caching support
- NoOpTranslator implemented
- GoogleTranslator implemented (googletrans)
- DeepLTranslator implemented (deepl)
- TranslationResult dataclass frozen
- Caching support added via RedisCache
- Multiple translator backends available
- Tests passing
- No known issues

**AI Implications**:
- TranslateBackend protocol is frozen (do not modify)
- Google and DeepL translators added via extension (Golden Rule 2)
- Do not modify TranslationResult structure (frozen)
- Can add more translator backends via extension

---

#### tts/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- TTS protocol defined
- TTSService implemented with caching support
- NoOpTTS implemented
- EdgeTTS implemented (edge-tts CLI)
- AzureTTS implemented (azure-cognitiveservices-speech)
- GoogleTTS implemented (gTTS)
- TTSResult dataclass frozen
- TTSConfig dataclass frozen with api_key and region support
- Caching support added via RedisCache
- Multiple TTS backends available
- Tests passing
- No known issues

**AI Implications**:
- TTS protocol is frozen (do not modify)
- Azure and Google TTS added via extension (Golden Rule 2)
- Do not modify TTSResult/TTSConfig structure (frozen)
- Can add more TTS backends via extension

---

#### mixer/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- MixerService implemented with protocol-based design
- MixerConfig dataclass frozen
- AudioMix dataclass frozen
- FFmpeg-based audio mixing implemented
- Volume level adjustment supported
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting MixerService (Golden Rule 1)
- Can add new mixing strategies via extension (Golden Rule 2)
- Do not modify MixerConfig/AudioMix structure (frozen)

---

#### render/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- Renderer implemented with protocol-based design
- RenderConfig dataclass frozen
- FFmpeg-based video rendering implemented
- Subtitle burning supported
- Quality presets implemented
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting Renderer (Golden Rule 1)
- Can add new codecs via extension (Golden Rule 2)
- Do not modify RenderConfig structure (frozen)

---

#### timeline/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- TimelineService implemented with protocol-based design
- TimelineConfig dataclass frozen
- TimelineSegment dataclass frozen
- SRT subtitle generation implemented
- VTT subtitle generation implemented
- Transcript alignment implemented
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting TimelineService (Golden Rule 1)
- Can add new subtitle formats via extension (Golden Rule 2)
- Do not modify TimelineConfig/TimelineSegment structure (frozen)

---

#### orchestrator/
**Status**: PARTIAL
**Completion Date**: TBD
**Known Facts**:
- LocalizationService implemented
- Service factory implemented
- LocalizationConfig dataclass frozen
- LocalizationResult dataclass frozen
- Basic workflow implemented
- May need enhancement

**AI Implications**:
- LocalizationService signature frozen (do not modify)
- Can add new workflow steps (Golden Rule 2)
- Do not modify config/result structures (frozen)

---

#### jobs/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- JobService implemented with protocol-based design
- JobQueue implemented with priority support
- Job dataclass frozen
- JobConfig dataclass frozen
- JobStatus enum frozen
- Background thread processing implemented
- Priority-based queue implemented
- Retry logic with exponential backoff
- Tests passing
- No known issues

**AI Implications**:
- JobService signature frozen (do not modify)
- Queue implementation added via extension (Golden Rule 2)
- Do not modify Job dataclass structure (frozen)
- Do not modify JobStatus enum (frozen)

---

#### bot/
**Status**: PARTIAL
**Completion Date**: TBD
**Known Facts**:
- TelegramBot implemented
- TelegramAdapter protocol defined
- RealTelegramAdapter implemented
- MockAdapter implemented
- Basic commands implemented
- Health check server implemented

**AI Implications**:
- TelegramAdapter protocol frozen (do not modify)
- Can add new commands (Golden Rule 2)
- Can add new adapters (Golden Rule 2)
- Do not modify command handler signatures

---

#### webhook/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- WebhookService implemented with protocol-based design
- WebhookEvent enum defined
- WebhookPayload dataclass frozen
- HTTP webhook delivery implemented
- Retry with exponential backoff implemented
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting WebhookService (Golden Rule 1)
- Can add signature verification via extension (Golden Rule 2)
- Do not modify WebhookEvent enum (frozen)

---

#### cache/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- RedisCache implemented with protocol-based design
- In-memory fallback when Redis unavailable
- CacheEntry dataclass frozen
- TTL support implemented
- Cache key generation implemented
- Tests passing
- No known issues

**AI Implications**:
- Do not suggest rewriting RedisCache (Golden Rule 1)
- Can add new caching strategies via extension (Golden Rule 2)
- Do not modify CacheEntry structure (frozen)

---

#### database/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- DatabaseManager implemented
- SQLite database working
- Schema frozen
- Connection pooling may be needed
- Tests passing
- No known issues

**AI Implications**:
- DatabaseManager signature frozen (do not modify)
- Schema is frozen (do not modify without migration)
- Can add connection pooling (Golden Rule 2)
- Can add new queries (Golden Rule 2)

---

#### config/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- Config dataclass frozen
- Configuration loading implemented
- No known issues

**AI Implications**:
- Config structure frozen (do not modify)
- Can add new config fields (Golden Rule 2)
- Can add validation logic (Golden Rule 2)

---

#### exceptions/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- Exception hierarchy defined
- Base exception class frozen
- No known issues

**AI Implications**:
- Exception hierarchy frozen (do not modify)
- Can add new exception classes (Golden Rule 2)

---

#### logger/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- Logger setup implemented
- Log level constants frozen
- No known issues

**AI Implications**:
- Logger setup signature frozen (do not modify)
- Can add new log handlers (Golden Rule 2)
- Can improve log formatting (Golden Rule 2)

---

#### models/
**Status**: COMPLETE
**Completion Date**: TBD
**Known Facts**:
- Shared data models defined
- Dataclass fields frozen
- No known issues

**AI Implications**:
- Dataclass fields frozen (do not modify)
- Can add new fields to non-frozen dataclasses (Golden Rule 2)
- Can add serialization methods (Golden Rule 2)

---

### Architecture Decisions (Known)

#### ADR-001: Dependency Injection
**Status**: IMPLEMENTED
**Known Facts**:
- All services use dependency injection
- No hardcoded dependencies
- Constructor injection pattern used

**AI Implications**:
- Always use dependency injection for new code
- Do not introduce hardcoded dependencies

---

#### ADR-004: Protocol-Based Design
**Status**: IMPLEMENTED
**Known Facts**:
- All cross-module interfaces use protocols
- SpeechBackend, TranslateBackend, TTS protocols defined
- Services depend on protocols, not concrete classes

**AI Implications**:
- Always use protocols for cross-module interfaces
- Do not depend on concrete implementations

---

#### ADR-006: Service Layer Pattern
**Status**: IMPLEMENTED
**Known Facts**:
- Service layer separates business logic from backends
- Adapters cannot bypass service layer
- Services orchestrate backend calls

**AI Implications**:
- Always go through service layer
- Do not bypass service layer from adapters

---

#### ADR-009: Composition Over Inheritance
**Status**: IMPLEMENTED
**Known Facts**:
- Dependency injection used instead of inheritance
- No deep inheritance hierarchies
- Protocol-based composition

**AI Implications**:
- Prefer composition over inheritance
- Use protocols for interfaces

---

#### ADR-010: Immutable Configuration
**Status**: IMPLEMENTED
**Known Facts**:
- Config dataclass frozen
- Configuration loaded once at startup
- No runtime config modification

**AI Implications**:
- Keep config immutable
- Do not modify config at runtime

---

### Known Issues

#### None Currently
**Status**: No known issues
**Last Updated**: TBD

**AI Implications**:
- Focus on new features
- Focus on incomplete modules
- No bug fixes needed currently

---

### Testing Status

#### Test Coverage
**Overall**: TBD%
**downloader/**: TBD%
**audio/**: TBD%
**speech/**: TBD%
**translate/**: TBD%
**tts/**: TBD%
**orchestrator/**: TBD%
**jobs/**: TBD%
**bot/**: TBD%
**database/**: TBD%

**AI Implications**:
- Always write tests for new code
- Aim for >80% coverage
- Follow testing guide (08_TESTING_GUIDE.md)

---

### Dependencies

#### External Libraries
**whisper**: Used for speech transcription
**googletrans**: Used for translation (planned)
**edge-tts**: Used for TTS
**demucs**: Used for vocal separation
**yt-dlp**: Used for video download

**AI Implications**:
- Do not add new dependencies without review
- Prefer libraries with stable APIs
- Document new dependencies in ADR

---

### Performance Characteristics

#### Known Performance Facts
- Download speed depends on network and platform
- Whisper transcription is CPU-intensive
- Demucs processing is GPU-intensive (if available)
- TTS synthesis is fast
- Video rendering is slow

**AI Implications**:
- Consider performance when adding features
- Use async I/O where appropriate
- Consider caching for expensive operations

---

### Security Considerations

#### Known Security Facts
- No user input validation issues known
- No SQL injection risks (parameterized queries)
- No XSS risks (no web UI yet)
- Secrets managed via environment variables

**AI Implications**:
- Always validate user input
- Use parameterized queries
- Never log secrets
- Follow security best practices

---

## How to Update This Document

When a module is completed or changed:
1. Update module status (COMPLETE/PARTIAL/TODO)
2. Add completion date
3. Add known facts about implementation
4. Add AI implications (what AI should know)
5. Update known issues if applicable
6. Update testing status if applicable

When an architectural decision is made:
1. Add to Architecture Decisions section
2. Document implementation status
3. Add AI implications

When issues are discovered:
1. Add to Known Issues section
2. Document impact
3. Add AI implications

## AI Behavior

When AI starts work:
1. Load this document (AI_MEMORY.md)
2. Check module completion status
3. Check known issues
4. Check AI implications for relevant modules
5. Do not read git history for this information

When AI makes changes:
1. Update module status if completed
2. Add new known facts
3. Update AI implications
4. Document any new issues discovered

This AI Memory prevents redundant questions and git history reads, making AI more efficient.
