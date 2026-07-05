# Universal Video AI - Professional Roadmap

## Overview
This roadmap defines 10 major milestones (~60 commits) to transform Universal Video AI into a production-ready system. Each milestone has clear objectives, commit-level breakdown, Definition of Done, and risk assessment.

## Definition of Done (DoD)

Every commit MUST meet these criteria before being considered complete:

### Code Quality
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] mypy passes (no type errors)
- [ ] ruff passes (no linting errors)
- [ ] Black formatted (code style consistent)

### Code Standards
- [ ] Logging added (no print() statements)
- [ ] No TODO comments left in code
- [ ] Type hints 100% coverage
- [ ] Backward compatible (no breaking changes)
- [ ] Public API unchanged (unless major version bump)

### Documentation
- [ ] Public API documented (docstrings)
- [ ] CHANGELOG.md updated
- [ ] Architecture decisions recorded (if needed)
- [ ] Module map updated (if permissions changed)

### Testing
- [ ] Unit tests added for new functionality
- [ ] Integration tests added for cross-module changes
- [ ] Test coverage >80% (core modules >90%)
- [ ] Tests follow naming conventions

### Review
- [ ] Code reviewed against PROJECT_BRAIN documents
- [ ] Dependency graph verified
- [ ] Module permissions respected
- [ ] Constitution followed

## Milestone 1: Dummy Backend Integration

**Objective**: Enable end-to-end pipeline with placeholder backends for testing

**Estimated Commits**: 6
**Estimated Time**: 2-3 days

### Context Window Rules
**Allowed to Read**:
- orchestrator/
- speech/
- translate/
- tts/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/ (read only if necessary)
- database/ (read only if necessary)
- deploy/

### Risk Assessment
**Risk**: None (placeholder implementation)
**Mitigation**: None needed

---

### Commit 24: LocalizationService Factory
**Objective**: Create factory function to build LocalizationService with configurable backends

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/factory.py` (create if not exists)

**Files Forbidden to Modify**:
- All service signatures
- All protocol definitions

**Acceptance Criteria**:
1. Factory function `create_localization_service()` exists
2. Accepts boolean flags for each backend (run_transcription, run_translation, run_tts)
3. Returns LocalizationService with appropriate backends injected
4. Default backends are NoOp implementations
5. Logger can be injected

**Unit Tests Required**:
- `test_orchestrator_factory.py` - Verify factory creates correct backend combinations

**Integration Tests Required**:
- None (factory only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 25: Dummy Whisper Backend
**Objective**: Implement NoOpSpeechBackend for placeholder transcription

**Files Allowed to Modify**:
- `src/universal_video_ai/speech/backend.py`

**Files Forbidden to Modify**:
- SpeechBackend protocol definition
- SpeechService signature

**Acceptance Criteria**:
1. NoOpSpeechBackend implements SpeechBackend protocol
2. transcribe() returns placeholder text "Dummy transcript"
3. Accepts audio_path and language parameters
4. Logs operation

**Unit Tests Required**:
- `test_dummy_speech_backend.py` - Verify NoOpSpeechBackend returns placeholder text

**Integration Tests Required**:
- None (backend only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 26: Dummy Translate Backend
**Objective**: Implement NoOpTranslateBackend for placeholder translation

**Files Allowed to Modify**:
- `src/universal_video_ai/translate/backend.py`

**Files Forbidden to Modify**:
- TranslateBackend protocol definition
- TranslateService signature

**Acceptance Criteria**:
1. NoOpTranslateBackend implements TranslateBackend protocol
2. translate() returns input text unchanged
3. Accepts text, source_lang, target_lang parameters
4. Logs operation

**Unit Tests Required**:
- `test_dummy_translate_backend.py` - Verify NoOpTranslateBackend returns input text

**Integration Tests Required**:
- None (backend only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 27: Dummy TTS Backend
**Objective**: Implement NoOpTTSBackend for placeholder speech synthesis

**Files Allowed to Modify**:
- `src/universal_video_ai/tts/backend.py`

**Files Forbidden to Modify**:
- TTSBackend protocol definition
- TTSService signature

**Acceptance Criteria**:
1. NoOpTTSBackend implements TTSBackend protocol
2. synthesize() creates placeholder audio file with header
3. Accepts text, output_path, language parameters
4. Logs operation

**Unit Tests Required**:
- `test_dummy_tts_backend.py` - Verify NoOpTTSBackend creates placeholder file

**Integration Tests Required**:
- None (backend only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 28: Pipeline Integration
**Objective**: Integrate dummy backends into LocalizationService

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/service.py`

**Files Forbidden to Modify**:
- LocalizationService signature
- LocalizationConfig structure
- LocalizationResult structure

**Acceptance Criteria**:
1. LocalizationService accepts dummy backends via constructor
2. Pipeline executes all steps with dummy backends
3. Final video generated with placeholder audio
4. No errors in end-to-end flow

**Unit Tests Required**:
- `test_localization_service_dummy.py` - Verify pipeline with dummy backends

**Integration Tests Required**:
- None (service only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 29: Telegram Integration
**Objective**: Inject LocalizationService into TelegramBot

**Files Allowed to Modify**:
- `scripts/run_bot.py`

**Files Forbidden to Modify**:
- TelegramBot signature
- Bot command handlers

**Acceptance Criteria**:
1. run_bot.py creates LocalizationService with dummy backends
2. LocalizationService injected into TelegramBot
3. /localize command uses LocalizationService
4. Bot starts without errors

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_bot_integration_dummy.py` - Full bot flow with dummy backends

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 30: Integration Tests
**Objective**: Add comprehensive integration tests for dummy pipeline

**Files Allowed to Modify**:
- `tests/test_localize_command_dummy.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test full /localize command flow
2. Verify final video generated
3. Verify database credits deducted
4. Verify job tracking works
5. All tests pass

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_localize_command_dummy.py` - Full /localize command with dummy backends

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 2: Real Whisper Integration

**Objective**: Replace dummy speech backend with real Whisper transcription

**Estimated Commits**: 8
**Estimated Time**: 3-4 days

### Context Window Rules
**Allowed to Read**:
- speech/
- audio/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- translate/
- tts/
- api/
- deploy/

### Risk Assessment
**Risk**: Whisper model loading fails or transcription quality is poor
**Mitigation**: Integration tests with sample audio, graceful fallback to dummy backend

---

### Commit 31: Enhance WhisperTranscriber
**Objective**: Improve WhisperTranscriber with better error handling and configuration

**Files Allowed to Modify**:
- `src/universal_video_ai/speech/whisper.py`

**Files Forbidden to Modify**:
- SpeechBackend protocol
- WhisperBackend class signature

**Acceptance Criteria**:
1. Lazy model loading (on first transcribe call)
2. GPU detection and automatic device selection
3. Better error messages for model failures
4. Configurable model size, device, task

**Unit Tests Required**:
- `test_whisper_transcriber.py` - Mock whisper.load_model, verify transcription flow

**Integration Tests Required**:
- None (transcriber only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 32: Improve WhisperBackend
**Objective**: Enhance WhisperBackend error handling and logging

**Files Allowed to Modify**:
- `src/universal_video_ai/speech/backend.py`

**Files Forbidden to Modify**:
- SpeechBackend protocol

**Acceptance Criteria**:
1. Convert Whisper exceptions to domain exceptions
2. Add detailed logging for transcription steps
3. Handle model unavailability gracefully
4. Validate audio file before transcription

**Unit Tests Required**:
- `test_whisper_backend.py` - Verify backend delegates to transcriber correctly

**Integration Tests Required**:
- None (backend only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 33: Whisper Configuration
**Objective**: Add Whisper configuration to .env.example

**Files Allowed to Modify**:
- `.env.example`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Add WHISPER_MODEL configuration
2. Add WHISPER_DEVICE configuration
3. Add WHISPER_TASK configuration
4. Document configuration options

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration documented
- [ ] CHANGELOG updated

---

### Commit 34: Factory Whisper Integration
**Objective**: Update factory to use WhisperBackend when transcription enabled

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/factory.py`

**Files Forbidden to Modify**:
- Factory function signature

**Acceptance Criteria**:
1. Factory creates WhisperBackend when run_transcription=True
2. WhisperBackend configured from environment
3. Falls back to NoOpSpeechBackend if Whisper unavailable

**Unit Tests Required**:
- `test_orchestrator_factory_whisper.py` - Verify Whisper backend creation

**Integration Tests Required**:
- None (factory only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 35: Transcribe Chinese Audio Test
**Objective**: Integration test with real Chinese audio sample

**Files Allowed to Modify**:
- `tests/test_transcribe_chinese_audio.py` (create)
- `tests/fixtures/audio/` (add sample_chinese.wav)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test with sample Chinese audio
2. Verify transcription quality (>80% accuracy)
3. Verify timestamps included
4. Test passes with real Whisper model

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_transcribe_chinese_audio.py` - Real Whisper transcription

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 36: Whisper Error Handling Test
**Objective**: Test behavior when Whisper model fails to load

**Files Allowed to Modify**:
- `tests/test_whisper_error_handling.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test graceful fallback when model unavailable
2. Test error message clarity
3. Test logging of errors
4. Test continues with dummy backend if needed

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_whisper_error_handling.py` - Model load failure handling

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 37: Whisper Config Test
**Objective**: Test Whisper configuration parsing

**Files Allowed to Modify**:
- `tests/test_whisper_config.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test configuration loading from environment
2. Test default values
3. Test invalid configuration handling
4. Test device detection

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] CHANGELOG updated

---

### Commit 38: Whisper Documentation
**Objective**: Document Whisper integration

**Files Allowed to Modify**:
- `README.md`
- `docs/` (if exists)

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Document Whisper installation
2. Document configuration options
3. Document model selection
4. Document GPU setup

**Unit Tests Required**:
- None (documentation only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Documentation complete
- [ ] CHANGELOG updated

---

## Milestone 3: Real Translation Integration

**Objective**: Replace dummy translation with Google Translate API

**Estimated Commits**: 7
**Estimated Time**: 2-3 days

### Context Window Rules
**Allowed to Read**:
- translate/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- speech/
- tts/
- api/
- deploy/

### Risk Assessment
**Risk**: Google Translate API rate limits or failures
**Mitigation**: Caching, rate limiting, graceful fallback to dummy backend

---

### Commit 39: Add GoogleTranslator
**Objective**: Implement GoogleTranslator using googletrans library

**Files Allowed to Modify**:
- `src/universal_video_ai/translate/translator.py`

**Files Forbidden to Modify**:
- Translator protocol
- NoOpTranslator implementation

**Acceptance Criteria**:
1. GoogleTranslator implements Translator protocol
2. Uses googletrans library
3. Handles API errors gracefully
4. Supports language codes

**Unit Tests Required**:
- `test_google_translator.py` - Mock GoogleTrans API, verify translation

**Integration Tests Required**:
- None (translator only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 40: Add GoogleTranslateBackend
**Objective**: Create adapter for GoogleTranslator

**Files Allowed to Modify**:
- `src/universal_video_ai/translate/backend.py`

**Files Forbidden to Modify**:
- TranslateBackend protocol

**Acceptance Criteria**:
1. GoogleTranslateBackend implements TranslateBackend protocol
2. Adapts GoogleTranslator to backend interface
3. Converts exceptions to domain errors
4. Logs translation operations

**Unit Tests Required**:
- `test_translate_backend.py` - Verify backend error conversion

**Integration Tests Required**:
- None (backend only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 41: Translation Caching
**Objective**: Add caching to TranslateService

**Files Allowed to Modify**:
- `src/universal_video_ai/translate/service.py`

**Files Forbidden to Modify**:
- TranslateService signature

**Acceptance Criteria**:
1. Cache translation results
2. Cache key includes text, source_lang, target_lang
3. Configurable TTL
4. Cache miss logs appropriately

**Unit Tests Required**:
- `test_translation_cache.py` - Verify caching logic

**Integration Tests Required**:
- None (service only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 42: Google API Configuration
**Objective**: Add Google Translate configuration to .env.example

**Files Allowed to Modify**:
- `.env.example`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Add TRANSLATION_PROVIDER configuration
2. Add GOOGLE_API_KEY configuration (if needed)
3. Document configuration options

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration documented
- [ ] CHANGELOG updated

---

### Commit 43: Factory Translation Integration
**Objective**: Update factory to use GoogleTranslateBackend when translation enabled

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/factory.py`

**Files Forbidden to Modify**:
- Factory function signature

**Acceptance Criteria**:
1. Factory creates GoogleTranslateBackend when run_translation=True
2. Backend configured from environment
3. Falls back to NoOpTranslateBackend if Google unavailable

**Unit Tests Required**:
- `test_orchestrator_factory_translate.py` - Verify Google backend creation

**Integration Tests Required**:
- None (factory only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 44: Translate Chinese to Vietnamese Test
**Objective**: Integration test with real Google Translate API

**Files Allowed to Modify**:
- `tests/test_translate_chinese_to_vietnamese.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test Chinese to Vietnamese translation
2. Verify translation quality
3. Test with real API key
4. Test passes

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_translate_chinese_to_vietnamese.py` - Real API translation

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 45: Translation Error Recovery Test
**Objective**: Test fallback when Google Translate API fails

**Files Allowed to Modify**:
- `tests/test_translation_error_recovery.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test graceful fallback on API failure
2. Test error message clarity
3. Test logging of errors
4. Test continues with dummy backend if needed

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_translation_error_recovery.py` - API failure handling

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 4: Real EdgeTTS Integration

**Objective**: Replace dummy TTS with EdgeTTS for Vietnamese speech

**Estimated Commits**: 6
**Estimated Time**: 2-3 days

### Context Window Rules
**Allowed to Read**:
- tts/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- speech/
- translate/
- api/
- deploy/

### Risk Assessment
**Risk**: EdgeTTS CLI not available or audio quality poor
**Mitigation**: Graceful fallback to dummy backend, audio quality tests

---

### Commit 46: Enhance EdgeTTS
**Objective**: Improve EdgeTTS implementation with better error handling

**Files Allowed to Modify**:
- `src/universal_video_ai/tts/tts.py`

**Files Forbidden to Modify**:
- TTS protocol
- EdgeTTS class signature

**Acceptance Criteria**:
1. Better subprocess error handling
2. Validate edge-tts CLI availability
3. Support multiple voices
4. Configurable output format

**Unit Tests Required**:
- `test_edge_tts.py` - Mock edge-tts subprocess, verify command construction

**Integration Tests Required**:
- None (TTS only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 47: Improve EdgeTTSBackend
**Objective**: Enhance EdgeTTSBackend error handling

**Files Allowed to Modify**:
- `src/universal_video_ai/tts/backend.py`

**Files Forbidden to Modify**:
- TTSBackend protocol

**Acceptance Criteria**:
1. Convert EdgeTTS exceptions to domain errors
2. Add detailed logging
3. Validate input text
4. Handle CLI unavailability gracefully

**Unit Tests Required**:
- `test_tts_backend.py` - Verify backend error handling

**Integration Tests Required**:
- None (backend only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 48: TTS Voice Configuration
**Objective**: Add TTS voice configuration to .env.example

**Files Allowed to Modify**:
- `.env.example`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Add TTS_LANGUAGE configuration
2. Add TTS_VOICE configuration
3. Add TTS_PROVIDER configuration
4. Document voice options

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration documented
- [ ] CHANGELOG updated

---

### Commit 49: Factory TTS Integration
**Objective**: Update factory to use EdgeTTSBackend when TTS enabled

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/factory.py`

**Files Forbidden to Modify**:
- Factory function signature

**Acceptance Criteria**:
1. Factory creates EdgeTTSBackend when run_tts=True
2. Backend configured from environment
3. Falls back to NoOpTTS if EdgeTTS unavailable

**Unit Tests Required**:
- `test_orchestrator_factory_tts.py` - Verify EdgeTTS backend creation

**Integration Tests Required**:
- None (factory only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 50: Synthesize Vietnamese Test
**Objective**: Integration test with real EdgeTTS for Vietnamese

**Files Allowed to Modify**:
- `tests/test_synthesize_vietnamese.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test Vietnamese text synthesis
2. Verify audio file created
3. Verify audio quality acceptable
4. Test passes with real EdgeTTS

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_synthesize_vietnamese.py` - Real EdgeTTS synthesis

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 51: TTS Audio Quality Test
**Objective**: Verify output audio properties

**Files Allowed to Modify**:
- `tests/test_tts_audio_quality.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Verify audio file format
2. Verify audio duration reasonable
3. Verify audio not corrupted
4. Test with different voices

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_tts_audio_quality.py` - Audio quality verification

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 5: Demucs Audio Separation

**Objective**: Enable vocal/background separation for better audio mixing

**Estimated Commits**: 8
**Estimated Time**: 3-4 days

### Context Window Rules
**Allowed to Read**:
- audio/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- speech/
- translate/
- tts/
- api/
- deploy/

### Risk Assessment
**Risk**: Demucs model loading fails or separation quality poor
**Mitigation**: Integration tests with sample audio, graceful fallback

---

### Commit 52: Enhance DemucsProcessor
**Objective**: Improve DemucsProcessor with better error handling

**Files Allowed to Modify**:
- `src/universal_video_ai/audio/demucs.py`

**Files Forbidden to Modify**:
- DemucsProcessor class signature
- DemucsConfig structure

**Acceptance Criteria**:
1. Lazy model loading
2. GPU detection and device selection
3. Better error messages
4. Configurable model and output format

**Unit Tests Required**:
- `test_demucs_processor.py` - Mock demucs subprocess, verify command

**Integration Tests Required**:
- None (processor only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 53: Demucs Configuration
**Objective**: Add Demucs configuration to .env.example

**Files Allowed to Modify**:
- `.env.example`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Add DEMUCS_MODEL configuration
2. Add DEMUCS_DEVICE configuration
3. Add ENABLE_DEMUCS configuration
4. Document configuration options

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration documented
- [ ] CHANGELOG updated

---

### Commit 54: Audio Pipeline Demucs Integration
**Objective**: Integrate Demucs into AudioPipeline

**Files Allowed to Modify**:
- `src/universal_video_ai/audio/pipeline.py`

**Files Forbidden to Modify**:
- AudioPipeline signature
- AudioPipelineConfig structure
- AudioPipelineResult structure

**Acceptance Criteria**:
1. AudioPipeline accepts DemucsProcessor
2. Runs Demucs when run_demucs=True
3. Includes DemucsOutput in result
4. Handles Demucs unavailability gracefully

**Unit Tests Required**:
- `test_audio_pipeline_demucs.py` - Verify pipeline integration

**Integration Tests Required**:
- None (pipeline only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 55: Factory Demucs Integration
**Objective**: Update factory to use Demucs when enabled

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/factory.py`

**Files Forbidden to Modify**:
- Factory function signature

**Acceptance Criteria**:
1. Factory creates DemucsProcessor when run_demucs=True
2. Processor configured from environment
3. Falls back gracefully if Demucs unavailable

**Unit Tests Required**:
- `test_orchestrator_factory_demucs.py` - Verify Demucs creation

**Integration Tests Required**:
- None (factory only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 56: Demucs Separation Test
**Objective**: Integration test with real Demucs

**Files Allowed to Modify**:
- `tests/test_demucs_separation.py` (create)
- `tests/fixtures/audio/` (add sample audio)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test with sample audio
2. Verify vocal/background separation
3. Verify output quality
4. Test passes with real Demucs

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_demucs_separation.py` - Real Demucs separation

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 57: Demucs Fallback Test
**Objective**: Test fallback when Demucs unavailable

**Files Allowed to Modify**:
- `tests/test_demucs_fallback.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test graceful fallback when Demucs unavailable
2. Test error message clarity
3. Test logging of errors
4. Test pipeline continues without Demucs

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_demucs_fallback.py` - Unavailable fallback

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 58: Demucs Config Test
**Objective**: Test Demucs configuration parsing

**Files Allowed to Modify**:
- `tests/test_demucs_config.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test configuration loading
2. Test default values
3. Test invalid configuration handling
4. Test device detection

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] CHANGELOG updated

---

### Commit 59: Demucs Documentation
**Objective**: Document Demucs integration

**Files Allowed to Modify**:
- `README.md`
- `docs/` (if exists)

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Document Demucs installation
2. Document configuration options
3. Document model selection
4. Document GPU setup

**Unit Tests Required**:
- None (documentation only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Documentation complete
- [ ] CHANGELOG updated

---

## Milestone 6: Job Queue System

**Objective**: Replace in-memory job service with Redis-backed queue

**Estimated Commits**: 10
**Estimated Time**: 4-5 days

### Context Window Rules
**Allowed to Read**:
- jobs/
- orchestrator/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/ (read only for integration)
- speech/
- translate/
- tts/
- api/

### Risk Assessment
**Risk**: Redis connection failures, queue processing errors
**Mitigation**: Connection pooling, retry logic, error recovery, integration tests

---

### Commit 60: Define JobQueue Protocol
**Objective**: Create JobQueue protocol for queue operations

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/queue.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. JobQueue protocol defined
2. Methods: enqueue, dequeue, get_status, cancel
3. Type hints for all methods
4. Docstrings for all methods

**Unit Tests Required**:
- None (protocol only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 61: Implement RedisQueue
**Objective**: Implement Redis-backed JobQueue

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/queue.py`

**Files Forbidden to Modify**:
- JobQueue protocol

**Acceptance Criteria**:
1. RedisQueue implements JobQueue protocol
2. Uses Redis for queue storage
3. Supports job priorities
4. Handles Redis connection errors

**Unit Tests Required**:
- `test_job_queue.py` - Verify Redis queue operations

**Integration Tests Required**:
- None (queue only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 62: Define Worker Protocol
**Objective**: Create Worker protocol for job processing

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/worker.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. Worker protocol defined
2. Methods: start, stop, process_job
3. Type hints for all methods
4. Docstrings for all methods

**Unit Tests Required**:
- None (protocol only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 63: Implement RedisWorker
**Objective**: Implement Redis-backed Worker

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/worker.py`

**Files Forbidden to Modify**:
- Worker protocol

**Acceptance Criteria**:
1. RedisWorker implements Worker protocol
2. Polls Redis for jobs
3. Executes job callbacks
4. Updates job status

**Unit Tests Required**:
- `test_job_worker.py` - Verify worker processing logic

**Integration Tests Required**:
- None (worker only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 64: Add Retry Logic
**Objective**: Implement retry with exponential backoff

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/worker.py`

**Files Forbidden to Modify**:
- Worker protocol

**Acceptance Criteria**:
1. Failed jobs retry with exponential backoff
2. Configurable max retries
3. Configurable backoff multiplier
4. Logging of retry attempts

**Unit Tests Required**:
- `test_retry_logic.py` - Verify retry with backoff

**Integration Tests Required**:
- None (worker only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 65: Refactor JobService to Use Queue
**Objective**: Update JobService to use JobQueue instead of threads

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/service.py`

**Files Forbidden to Modify**:
- JobService public interface

**Acceptance Criteria**:
1. JobService accepts JobQueue
2. Enqueues jobs instead of spawning threads
3. Maintains backward compatibility
4. Deprecates old thread-based method

**Unit Tests Required**:
- `test_job_service_queue.py` - Verify queue integration

**Integration Tests Required**:
- None (service only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 66: Add Job Cancellation
**Objective**: Implement job cancellation support

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/queue.py`
- `src/universal_video_ai/jobs/worker.py`

**Files Forbidden to Modify**:
- JobQueue protocol
- Worker protocol

**Acceptance Criteria**:
1. JobQueue supports cancellation
2. Worker checks for cancellation
3. Job status updated to CANCELLED
4. Resources cleaned up on cancellation

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_job_cancellation.py` - Cancel in-flight jobs

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 67: Create run_worker.py Script
**Objective**: Create worker startup script

**Files Allowed to Modify**:
- `scripts/run_worker.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. Script starts RedisWorker
2. Accepts configuration from environment
3. Handles graceful shutdown
4. Logs worker activity

**Unit Tests Required**:
- None (script only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 68: Update docker-compose.prod.yml
**Objective**: Add worker service to Docker Compose

**Files Allowed to Modify**:
- `docker-compose.prod.yml`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Add worker service
2. Configure Redis connection
3. Configure worker count
4. Add health checks

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration complete
- [ ] CHANGELOG updated

---

### Commit 69: Queue Integration Test
**Objective**: Full queue → worker flow test

**Files Allowed to Modify**:
- `tests/test_queue_integration.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test job enqueue
2. Test worker processing
3. Test job completion
4. Test error handling

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_queue_integration.py` - Full queue flow

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 7: Monitoring & Metrics

**Objective**: Add production monitoring and metrics collection

**Estimated Commits**: 8
**Estimated Time**: 3-4 days

### Context Window Rules
**Allowed to Read**:
- monitoring/
- jobs/
- orchestrator/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- speech/
- translate/
- tts/
- api/

### Risk Assessment
**Risk**: Metrics overhead affects performance
**Mitigation**: Sampling, async metrics, performance tests

---

### Commit 70: Define MetricsCollector Protocol
**Objective**: Create protocol for metrics collection

**Files Allowed to Modify**:
- `src/universal_video_ai/monitoring/metrics.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. MetricsCollector protocol defined
2. Methods: increment, gauge, timing, histogram
3. Type hints for all methods
4. Docstrings for all methods

**Unit Tests Required**:
- None (protocol only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 71: Implement PrometheusMetrics
**Objective**: Implement Prometheus metrics collector

**Files Allowed to Modify**:
- `src/universal_video_ai/monitoring/metrics.py`

**Files Forbidden to Modify**:
- MetricsCollector protocol

**Acceptance Criteria**:
1. PrometheusMetrics implements MetricsCollector protocol
2. Uses prometheus_client library
3. Exposes metrics on HTTP endpoint
4. Supports metric labels

**Unit Tests Required**:
- `test_metrics_collector.py` - Verify metric recording

**Integration Tests Required**:
- None (metrics only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 72: Define AlertManager Protocol
**Objective**: Create protocol for alert management

**Files Allowed to Modify**:
- `src/universal_video_ai/monitoring/alerts.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. AlertManager protocol defined
2. Methods: check_threshold, send_alert
3. Type hints for all methods
4. Docstrings for all methods

**Unit Tests Required**:
- None (protocol only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 73: Implement ThresholdAlertManager
**Objective**: Implement threshold-based alert manager

**Files Allowed to Modify**:
- `src/universal_video_ai/monitoring/alerts.py`

**Files Forbidden to Modify**:
- AlertManager protocol

**Acceptance Criteria**:
1. ThresholdAlertManager implements AlertManager protocol
2. Checks metric thresholds
3. Sends alerts via webhook
4. Configurable thresholds

**Unit Tests Required**:
- `test_alert_manager.py` - Verify alert triggering

**Integration Tests Required**:
- None (alerts only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 74: Add Metrics Hooks to JobService
**Objective**: Add metrics collection to JobService

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/service.py`

**Files Forbidden to Modify**:
- JobService public interface

**Acceptance Criteria**:
1. JobService accepts MetricsCollector
2. Tracks job execution time
3. Tracks job success/failure rates
4. Tracks queue depth

**Unit Tests Required**:
- `test_metrics_hooks.py` - Verify service integration

**Integration Tests Required**:
- None (service only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 75: Add Metrics Hooks to LocalizationService
**Objective**: Add metrics collection to LocalizationService

**Files Allowed to Modify**:
- `src/universal_video_ai/orchestrator/service.py`

**Files Forbidden to Modify**:
- LocalizationService public interface

**Acceptance Criteria**:
1. LocalizationService accepts MetricsCollector
2. Tracks pipeline step durations
3. Tracks API call counts
4. Tracks error rates

**Unit Tests Required**:
- `test_metrics_hooks.py` - Verify service integration

**Integration Tests Required**:
- None (service only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 76: Add Metrics Hooks to TelegramBot
**Objective**: Add metrics collection to TelegramBot

**Files Allowed to Modify**:
- `src/universal_video_ai/bot/telegram_bot.py`

**Files Forbidden to Modify**:
- TelegramBot public interface

**Acceptance Criteria**:
1. TelegramBot accepts MetricsCollector
2. Tracks command counts
3. Tracks response times
4. Tracks error rates

**Unit Tests Required**:
- `test_metrics_hooks.py` - Verify service integration

**Integration Tests Required**:
- None (service only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 77: Prometheus Endpoint Test
**Objective**: Test Prometheus metrics endpoint

**Files Allowed to Modify**:
- `tests/test_prometheus_endpoint.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test metrics endpoint accessible
2. Test metrics format correct
3. Test metric labels present
4. Test metric values accurate

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_prometheus_endpoint.py` - Metrics export

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 8: Webhook System

**Objective**: Add webhook notifications for job events

**Estimated Commits**: 5
**Estimated Time**: 2-3 days

### Context Window Rules
**Allowed to Read**:
- webhook/
- jobs/
- database/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- speech/
- translate/
- tts/
- api/

### Risk Assessment
**Risk**: Webhook delivery failures, signature verification bypass
**Mitigation**: Retry logic, signature verification, integration tests

---

### Commit 78: Define WebhookDispatcher Protocol
**Objective**: Create protocol for webhook delivery

**Files Allowed to Modify**:
- `src/universal_video_ai/webhook/dispatcher.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. WebhookDispatcher protocol defined
2. Methods: dispatch, verify_signature
3. Type hints for all methods
4. Docstrings for all methods

**Unit Tests Required**:
- None (protocol only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 79: Implement HttpWebhookDispatcher
**Objective**: Implement HTTP webhook dispatcher

**Files Allowed to Modify**:
- `src/universal_video_ai/webhook/dispatcher.py`

**Files Forbidden to Modify**:
- WebhookDispatcher protocol

**Acceptance Criteria**:
1. HttpWebhookDispatcher implements WebhookDispatcher protocol
2. Sends webhooks via HTTP POST
3. Supports retry with backoff
4. Logs delivery attempts

**Unit Tests Required**:
- `test_webhook_dispatcher.py` - Verify webhook delivery

**Integration Tests Required**:
- None (dispatcher only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 80: Implement Signature Verification
**Objective**: Add HMAC signature verification

**Files Allowed to Modify**:
- `src/universal_video_ai/webhook/signature.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. Signature verification protocol defined
2. HMAC-SHA256 implementation
3. Configurable secret key
4. Timing-safe comparison

**Unit Tests Required**:
- `test_signature_verification.py` - Verify HMAC verification

**Integration Tests Required**:
- None (verification only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 81: Add Webhook Storage to Database
**Objective**: Add webhook registration to database

**Files Allowed to Modify**:
- `src/universal_video_ai/database/`

**Files Forbidden to Modify**:
- DatabaseManager public interface

**Acceptance Criteria**:
1. Add webhooks table
2. Methods: register_webhook, get_webhooks, delete_webhook
3. Per-user webhook storage
4. Webhook URL validation

**Unit Tests Required**:
- None (database only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 82: Add Webhook Hooks to JobService
**Objective**: Trigger webhooks on job events

**Files Allowed to Modify**:
- `src/universal_video_ai/jobs/service.py`

**Files Forbidden to Modify**:
- JobService public interface

**Acceptance Criteria**:
1. JobService accepts WebhookDispatcher
2. Triggers webhook on job completion
3. Includes job details in payload
4. Handles webhook failures gracefully

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_webhook_e2e.py` - Full webhook flow

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 9: Admin API

**Objective**: Add REST API for admin operations

**Estimated Commits**: 10
**Estimated Time**: 4-5 days

### Context Window Rules
**Allowed to Read**:
- api/
- jobs/
- database/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- bot/
- speech/
- translate/
- tts/
- webhook/

### Risk Assessment
**Risk**: API security vulnerabilities, unauthorized access
**Mitigation**: Authentication, rate limiting, security audit

---

### Commit 83: Define API Handler Protocols
**Objective**: Create protocols for API handlers

**Files Allowed to Modify**:
- `src/universal_video_ai/api/handlers.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. Handler protocols defined
2. Methods: handle_request, validate_auth
3. Type hints for all methods
4. Docstrings for all methods

**Unit Tests Required**:
- None (protocol only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 84: Implement Auth Middleware
**Objective**: Create authentication middleware

**Files Allowed to Modify**:
- `src/universal_video_ai/api/middleware.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. Auth middleware protocol defined
2. JWT token validation
3. API key validation
4. Rate limiting support

**Unit Tests Required**:
- `test_auth_middleware.py` - Verify authentication

**Integration Tests Required**:
- None (middleware only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 85: Implement Job Endpoints
**Objective**: Add job management endpoints

**Files Allowed to Modify**:
- `src/universal_video_ai/api/handlers.py`

**Files Forbidden to Modify**:
- Handler protocols

**Acceptance Criteria**:
1. GET /api/jobs - List jobs
2. GET /api/jobs/{id} - Get job details
3. POST /api/jobs - Submit job
4. DELETE /api/jobs/{id} - Cancel job

**Unit Tests Required**:
- `test_api_handlers.py` - Verify each endpoint

**Integration Tests Required**:
- None (handlers only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 86: Implement User Endpoints
**Objective**: Add user management endpoints

**Files Allowed to Modify**:
- `src/universal_video_ai/api/handlers.py`

**Files Forbidden to Modify**:
- Handler protocols

**Acceptance Criteria**:
1. GET /api/users - List users
2. GET /api/users/{id} - Get user details
3. POST /api/users/{id}/credits - Add credits

**Unit Tests Required**:
- `test_api_handlers.py` - Verify each endpoint

**Integration Tests Required**:
- None (handlers only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 87: Implement Metrics Endpoint
**Objective**: Add metrics endpoint

**Files Allowed to Modify**:
- `src/universal_video_ai/api/handlers.py`

**Files Forbidden to Modify**:
- Handler protocols

**Acceptance Criteria**:
1. GET /api/metrics - System metrics
2. Returns JSON metrics
3. Requires authentication

**Unit Tests Required**:
- `test_api_handlers.py` - Verify metrics endpoint

**Integration Tests Required**:
- None (handlers only)

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 88: Create run_api.py Script
**Objective**: Create API server script

**Files Allowed to Modify**:
- `scripts/run_api.py` (create)

**Files Forbidden to Modify**:
- None (new file)

**Acceptance Criteria**:
1. Script starts API server
2. Accepts configuration from environment
3. Handles graceful shutdown
4. Logs API activity

**Unit Tests Required**:
- None (script only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] Type hints 100%
- [ ] CHANGELOG updated

---

### Commit 89: Add OpenAPI Documentation
**Objective**: Add Swagger/OpenAPI documentation

**Files Allowed to Modify**:
- `src/universal_video_ai/api/` (add openapi.yaml)

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. OpenAPI spec for all endpoints
2. Request/response schemas
3. Authentication documentation
4. Interactive API docs

**Unit Tests Required**:
- None (documentation only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Documentation complete
- [ ] CHANGELOG updated

---

### Commit 90: Update docker-compose.prod.yml
**Objective**: Add API service to Docker Compose

**Files Allowed to Modify**:
- `docker-compose.prod.yml`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Add API service
2. Configure authentication
3. Configure rate limiting
4. Add health checks

**Unit Tests Required**:
- None (configuration only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration complete
- [ ] CHANGELOG updated

---

### Commit 91: API E2E Test
**Objective**: Full API workflow test

**Files Allowed to Modify**:
- `tests/test_api_e2e.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test job submission via API
2. Test job status check
3. Test user credit management
4. Test authentication enforcement

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_api_e2e.py` - Full API workflow

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

### Commit 92: API Security Test
**Objective**: Test API security

**Files Allowed to Modify**:
- `tests/test_api_security.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Test authentication required
2. Test rate limiting enforced
3. Test authorization checks
4. Test input validation

**Unit Tests Required**:
- None (integration only)

**Integration Tests Required**:
- `test_api_security.py` - Security verification

**DoD Checklist**:
- [ ] Integration tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

---

## Milestone 10: Production Hardening

**Objective**: Final production deployment and optimization

**Estimated Commits**: 8
**Estimated Time**: 3-4 days

### Context Window Rules
**Allowed to Read**:
- Dockerfile
- docker-compose.prod.yml
- nginx.conf
- scripts/
- tests/
- PROJECT_BRAIN/

**Forbidden to Read**:
- Core application code (only optimizations)

### Risk Assessment
**Risk**: Deployment failures, performance issues
**Mitigation**: Staging deployment, load testing, monitoring

---

### Commit 93: Optimize Dockerfile
**Objective**: Optimize Docker image size and build time

**Files Allowed to Modify**:
- `Dockerfile`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Multi-stage build
2. Image size <2GB
3. Layer caching optimized
4. Security scanning passes

**Unit Tests Required**:
- None (infrastructure only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Image built successfully
- [ ] Size <2GB
- [ ] CHANGELOG updated

---

### Commit 94: Finalize docker-compose.prod.yml
**Objective**: Complete production Docker Compose configuration

**Files Allowed to Modify**:
- `docker-compose.prod.yml`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. All services configured
2. Resource limits set
3. Health checks configured
4. Logging configured

**Unit Tests Required**:
- None (infrastructure only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration complete
- [ ] CHANGELOG updated

---

### Commit 95: Configure nginx
**Objective**: Set up reverse proxy with nginx

**Files Allowed to Modify**:
- `nginx.conf`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Reverse proxy configured
2. SSL/TLS termination
3. Rate limiting
4. Static file serving

**Unit Tests Required**:
- None (infrastructure only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Configuration complete
- [ ] CHANGELOG updated

---

### Commit 96: Add Health Checks
**Objective**: Implement comprehensive health checks

**Files Allowed to Modify**:
- `src/universal_video_ai/bot/server.py`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Database health check
2. Redis health check
3. Service health checks
4. Health endpoint returns 200 if healthy

**Unit Tests Required**:
- `test_health_checks.py` - Verify health endpoints

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] CHANGELOG updated

---

### Commit 97: Graceful Shutdown
**Objective**: Implement graceful shutdown handling

**Files Allowed to Modify**:
- `scripts/run_bot.py`
- `scripts/run_worker.py`
- `scripts/run_api.py`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Handle SIGTERM/SIGINT
2. Complete in-flight jobs
3. Close connections
4. Clean up resources

**Unit Tests Required**:
- `test_graceful_shutdown.py` - Verify clean shutdown

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Unit tests pass
- [ ] mypy passes
- [ ] ruff passes
- [ ] Black formatted
- [ ] CHANGELOG updated

---

### Commit 98: Load Testing
**Objective**: Load test with 100 concurrent jobs

**Files Allowed to Modify**:
- `tests/test_load_100_concurrent.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. 100 concurrent jobs processed
2. No job failures
3. Response time acceptable
4. Resource usage reasonable

**Unit Tests Required**:
- None (load test only)

**Integration Tests Required**:
- `test_load_100_concurrent.py` - Load test

**DoD Checklist**:
- [ ] Load test passes
- [ ] CHANGELOG updated

---

### Commit 99: Disaster Recovery Test
**Objective**: Test backup and recovery procedures

**Files Allowed to Modify**:
- `tests/test_disaster_recovery.py` (create)

**Files Forbidden to Modify**:
- Production code

**Acceptance Criteria**:
1. Database backup works
2. Database restore works
3. Data integrity verified
4. Recovery time acceptable

**Unit Tests Required**:
- None (disaster recovery only)

**Integration Tests Required**:
- `test_disaster_recovery.py` - Backup/restore

**DoD Checklist**:
- [ ] Disaster recovery test passes
- [ ] CHANGELOG updated

---

### Commit 100: Deployment Documentation
**Objective**: Complete deployment documentation

**Files Allowed to Modify**:
- `DEPLOYMENT.md`
- `README.md`

**Files Forbidden to Modify**:
- None

**Acceptance Criteria**:
1. Deployment steps documented
2. Environment variables documented
3. Troubleshooting guide
4. Monitoring setup guide

**Unit Tests Required**:
- None (documentation only)

**Integration Tests Required**:
- None

**DoD Checklist**:
- [ ] Documentation complete
- [ ] CHANGELOG updated

---

## Summary

**Total Milestones**: 10
**Total Commits**: ~100
**Total Estimated Time**: ~40-50 days

**Key Principles**:
- Each commit has clear objective and DoD
- Context window rules reduce token usage
- Risk assessment with mitigation
- Comprehensive testing required
- Architecture frozen and stable

**Execution Guidelines**:
1. Follow commit order within milestones
2. Complete DoD before next commit
3. Respect context window rules
4. Follow PROJECT_BRAIN documents
5. Update CHANGELOG for each commit

This roadmap ensures consistent, high-quality development regardless of which AI or developer works on the project.
