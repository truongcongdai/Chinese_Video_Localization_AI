# Universal Video AI - Professional Roadmap

## ⚠️ IMPORTANT - This document is DEPRECATED

**This roadmap has been superseded by the enhanced PROJECT_BRAIN documentation.**

**Please use**: `PROJECT_BRAIN/02_ROADMAP.md` for the complete, commit-level roadmap with Definition of Done, risk assessment, and context window rules.

## PROJECT_BRAIN Documentation

The PROJECT_BRAIN directory contains the complete architectural brain of the project:

- **PROJECT_BRAIN/01_ARCHITECTURE.md** - Core architecture documentation
- **PROJECT_BRAIN/02_ROADMAP.md** - Enhanced roadmap with DoD (100 commits, detailed breakdown)
- **PROJECT_BRAIN/03_DECISIONS.md** - Architecture decision records (12 ADRs)
- **PROJECT_BRAIN/04_CONSTITUTION.md** - Coding rules and standards
- **PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md** - Service dependency graph
- **PROJECT_BRAIN/06_MODULE_MAP.md** - Module boundaries and permissions
- **PROJECT_BRAIN/07_PUBLIC_API.md** - Public API contracts
- **PROJECT_BRAIN/08_TESTING_GUIDE.md** - Testing standards
- **PROJECT_BRAIN/09_AI_RULES.md** - AI development guidelines
- **PROJECT_BRAIN/10_CHANGELOG.md** - Change tracking

## Why PROJECT_BRAIN?

The PROJECT_BAIN approach provides:
- **Definition of Done** for every commit
- **Context window rules** to reduce AI token usage
- **Risk assessment** for each milestone
- **Commit-level breakdown** (100 commits vs 60)
- **AI-specific rules** for consistent development
- **Enterprise-grade documentation** for any AI assistant

## Quick Start

For AI assistants working on this project:
1. Load all PROJECT_BRAIN/*.md files as context
2. Reference PROJECT_BRAIN/02_ROADMAP.md for current milestone
3. Follow PROJECT_BRAIN/09_AI_RULES.md for development guidelines
4. Check PROJECT_BRAIN/06_MODULE_MAP.md for file permissions
5. Verify PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md for dependency rules

---

## Legacy Content (For Reference Only)

The following content is preserved for reference but should not be used for active development. Use PROJECT_BRAIN/02_ROADMAP.md instead.

## Overview
This roadmap defines 10 major milestones (~60 commits) to transform Universal Video AI into a production-ready system. Each milestone has clear objectives, interface definitions, file permissions, acceptance criteria, and testing requirements.

**Architecture Principles**:
- Protocol-based design for flexibility
- Service layer separation of concerns
- Immutable core interfaces
- Test-driven development
- Production-ready monitoring

---

## Stable Core (DO NOT MODIFY)
These files form the foundation and should not be changed without architectural review:

### Core Interfaces
- `src/universal_video_ai/exceptions.py` - Base exception hierarchy
- `src/universal_video_ai/config.py` - Configuration management
- `src/universal_video_ai/logger.py` - Logging infrastructure

### Protocol Definitions (Immutable)
- `src/universal_video_ai/speech/backend.py` - `SpeechBackend` protocol
- `src/universal_video_ai/translate/backend.py` - `TranslateBackend` protocol
- `src/universal_video_ai/tts/backend.py` - `TTSBackend` protocol
- `src/universal_video_ai/tts/tts.py` - `TTS` protocol
- `src/universal_video_ai/translate/translator.py` - `Translator` protocol

### Service Layer Contracts (Immutable Signatures)
- `src/universal_video_ai/downloader/service.py` - `DownloadService` class signature
- `src/universal_video_ai/speech/service.py` - `SpeechService` class signature
- `src/universal_video_ai/translate/service.py` - `TranslateService` class signature
- `src/universal_video_ai/tts/service.py` - `TTSService` class signature
- `src/universal_video_ai/orchestrator/service.py` - `LocalizationService` class signature
- `src/universal_video_ai/jobs/service.py` - `JobService` class signature
- `src/universal_video_ai/database/` - Database manager interface

### Data Models (Immutable Fields)
- `src/universal_video_ai/jobs/models.py` - Job dataclasses
- `src/universal_video_ai/downloader/download_result.py` - DownloadResult
- `src/universal_video_ai/audio/audio_result.py` - AudioResult

---

## Milestone 1: Dummy Backend Integration
**Objective**: Enable end-to-end pipeline with placeholder backends for testing

### New Interfaces Required
None (use existing protocols)

### Files Allowed to Modify
- `src/universal_video_ai/orchestrator/factory.py` - Add dummy backend creation
- `src/universal_video_ai/speech/backend.py` - Implement NoOpSpeechBackend
- `src/universal_video_ai/translate/backend.py` - Implement NoOpTranslateBackend
- `src/universal_video_ai/tts/backend.py` - Implement NoOpTTSBackend
- `scripts/run_bot.py` - Inject LocalizationService with dummy backends
- `tests/` - Add integration tests

### Files Forbidden to Modify
- All protocol definitions
- All service layer signatures
- Core data models

### Acceptance Criteria
1. `/localize <url>` command completes successfully with dummy backends
2. Final video file generated with placeholder audio
3. All pipeline steps execute without errors
4. Database credits deducted correctly
5. Job tracking works end-to-end

### Unit Tests Required
- `test_dummy_speech_backend.py` - Verify NoOpSpeechBackend returns placeholder text
- `test_dummy_translate_backend.py` - Verify NoOpTranslateBackend returns input text
- `test_dummy_tts_backend.py` - Verify NoOpTTSBackend creates placeholder audio file
- `test_orchestrator_factory.py` - Verify factory creates correct backend combinations

### Integration Tests Required
- `test_localize_command_dummy.py` - Full `/localize` command flow with dummy backends
- `test_bot_integration_dummy.py` - Bot receives URL, processes, returns video

### Estimated Commits: 4-6

---

## Milestone 2: Real Whisper Integration
**Objective**: Replace dummy speech backend with real Whisper transcription

### New Interfaces Required
None (use existing SpeechBackend protocol)

### Files Allowed to Modify
- `src/universal_video_ai/speech/whisper.py` - Enhance WhisperTranscriber
- `src/universal_video_ai/speech/backend.py` - Improve WhisperBackend error handling
- `src/universal_video_ai/orchestrator/factory.py` - Add Whisper backend option
- `.env.example` - Add Whisper configuration
- `tests/` - Add Whisper-specific tests

### Files Forbidden to Modify
- SpeechBackend protocol definition
- SpeechService signature
- Other service layers

### Acceptance Criteria
1. Whisper model loads successfully (base model by default)
2. Chinese audio transcribed to text with >80% accuracy
3. Transcription includes timestamps
4. Model loading is lazy (on first use)
5. GPU detection and automatic device selection
6. Graceful fallback when Whisper unavailable

### Unit Tests Required
- `test_whisper_transcriber.py` - Mock whisper.load_model, verify transcription flow
- `test_whisper_backend.py` - Verify backend delegates to transcriber correctly
- `test_whisper_config.py` - Verify configuration parsing

### Integration Tests Required
- `test_transcribe_chinese_audio.py` - Use sample Chinese audio, verify output
- `test_whisper_error_handling.py` - Test behavior when model fails to load

### Estimated Commits: 6-8

---

## Milestone 3: Real Translation Integration
**Objective**: Replace dummy translation with Google Translate API

### New Interfaces Required
None (use existing TranslateBackend protocol)

### Files Allowed to Modify
- `src/universal_video_ai/translate/translator.py` - Add GoogleTranslator implementation
- `src/universal_video_ai/translate/backend.py` - Add GoogleTranslateBackend adapter
- `src/universal_video_ai/orchestrator/factory.py` - Add Google translation option
- `.env.example` - Add Google API key configuration
- `requirements.txt` - Add googletrans library
- `tests/` - Add translation tests

### Files Forbidden to Modify
- TranslateBackend protocol definition
- TranslateService signature
- Other service layers

### Acceptance Criteria
1. Chinese text translated to Vietnamese with acceptable quality
2. API key loaded from environment
3. Rate limiting implemented (Google API limits)
4. Caching of translation results
5. Error handling for API failures
6. Fallback to dummy backend if API unavailable

### Unit Tests Required
- `test_google_translator.py` - Mock GoogleTrans API, verify translation
- `test_translate_backend.py` - Verify backend error conversion
- `test_translation_cache.py` - Verify caching logic

### Integration Tests Required
- `test_translate_chinese_to_vietnamese.py` - Real API call with test key
- `test_translation_error_recovery.py` - Simulate API failure, verify fallback

### Estimated Commits: 5-7

---

## Milestone 4: Real EdgeTTS Integration
**Objective**: Replace dummy TTS with EdgeTTS for Vietnamese speech

### New Interfaces Required
None (use existing TTSBackend protocol)

### Files Allowed to Modify
- `src/universal_video_ai/tts/tts.py` - Enhance EdgeTTS implementation
- `src/universal_video_ai/tts/backend.py` - Improve EdgeTTSBackend
- `src/universal_video_ai/orchestrator/factory.py` - Add EdgeTTS option
- `.env.example` - Add TTS voice configuration
- `tests/` - Add TTS tests

### Files Forbidden to Modify
- TTSBackend protocol definition
- TTSService signature
- Other service layers

### Acceptance Criteria
1. Vietnamese text synthesized to audio file
2. Voice selection from configuration
3. Audio quality acceptable for video
4. Multiple voice options supported
5. Error handling for edge-tts CLI failures
6. Output format configurable (mp3/wav)

### Unit Tests Required
- `test_edge_tts.py` - Mock edge-tts subprocess, verify command construction
- `test_tts_backend.py` - Verify backend error handling
- `test_tts_config.py` - Verify voice configuration

### Integration Tests Required
- `test_synthesize_vietnamese.py` - Real EdgeTTS call with Vietnamese text
- `test_tts_audio_quality.py` - Verify output audio properties

### Estimated Commits: 4-6

---

## Milestone 5: Demucs Audio Separation
**Objective**: Enable vocal/background separation for better audio mixing

### New Interfaces Required
None (use existing DemucsProcessor)

### Files Allowed to Modify
- `src/universal_video_ai/audio/demucs.py` - Enhance DemucsProcessor
- `src/universal_video_ai/audio/pipeline.py` - Integrate Demucs into pipeline
- `src/universal_video_ai/orchestrator/service.py` - Use Demucs output in mixing
- `.env.example` - Add Demucs configuration
- `tests/` - Add Demucs tests

### Files Forbidden to Modify
- AudioPipelineConfig signature
- AudioPipelineResult signature
- Other service layers

### Acceptance Criteria
1. Audio separated into vocals and background
2. Demucs model loads successfully
3. GPU detection and automatic device selection
4. Output quality acceptable for mixing
5. Graceful degradation when Demucs unavailable
6. Configurable model (htdemucs, htdemucs_ft, etc.)

### Unit Tests Required
- `test_demucs_processor.py` - Mock demucs subprocess, verify command
- `test_demucs_config.py` - Verify configuration parsing
- `test_audio_pipeline_demucs.py` - Verify pipeline integration

### Integration Tests Required
- `test_demucs_separation.py` - Real Demucs call with sample audio
- `test_demucs_fallback.py` - Verify fallback when Demucs unavailable

### Estimated Commits: 6-8

---

## Milestone 6: Job Queue System
**Objective**: Replace in-memory job service with Redis-backed queue

### New Interfaces Required
- `src/universal_video_ai/jobs/queue.py` - JobQueue protocol
- `src/universal_video_ai/jobs/worker.py` - Worker protocol

### Files Allowed to Modify
- `src/universal_video_ai/jobs/` - Add queue and worker implementations
- `src/universal_video_ai/jobs/service.py` - Refactor to use queue
- `src/universal_video_ai/bot/telegram_bot.py` - Submit jobs to queue
- `scripts/run_worker.py` - New worker script
- `docker-compose.prod.yml` - Add worker service
- `tests/` - Add queue tests

### Files Forbidden to Modify
- Job data models
- JobService public interface (keep compatible)
- Bot command handlers

### Acceptance Criteria
1. Jobs submitted to Redis queue
2. Worker processes jobs from queue
3. Multiple workers can run concurrently
4. Job status updates persisted
5. Failed jobs retry with exponential backoff
6. Queue priority support
7. Job cancellation support

### Unit Tests Required
- `test_job_queue.py` - Verify Redis queue operations
- `test_job_worker.py` - Verify worker processing logic
- `test_retry_logic.py` - Verify retry with backoff

### Integration Tests Required
- `test_queue_integration.py` - Full queue → worker flow
- `test_multi_worker.py` - Multiple workers processing jobs
- `test_job_cancellation.py` - Cancel in-flight jobs

### Estimated Commits: 8-10

---

## Milestone 7: Monitoring & Metrics
**Objective**: Add production monitoring and metrics collection

### New Interfaces Required
- `src/universal_video_ai/monitoring/metrics.py` - MetricsCollector protocol
- `src/universal_video_ai/monitoring/alerts.py` - AlertManager protocol

### Files Allowed to Modify
- `src/universal_video_ai/monitoring/` - Implement metrics and alerts
- `src/universal_video_ai/jobs/service.py` - Add metrics hooks
- `src/universal_video_ai/orchestrator/service.py` - Add metrics hooks
- `src/universal_video_ai/bot/telegram_bot.py` - Add metrics hooks
- `scripts/run_bot.py` - Initialize monitoring
- `docker-compose.prod.yml` - Add Prometheus/Grafana
- `tests/` - Add monitoring tests

### Files Forbidden to Modify
- Service layer signatures (only add metrics hooks)
- Core business logic

### Acceptance Criteria
1. Job execution time tracked
2. API call counts and latencies tracked
3. Error rates monitored
4. Resource usage (CPU, memory) tracked
5. Custom metrics for business logic
6. Alert on threshold breaches
7. Prometheus endpoint available
8. Grafana dashboards configured

### Unit Tests Required
- `test_metrics_collector.py` - Verify metric recording
- `test_alert_manager.py` - Verify alert triggering
- `test_metrics_hooks.py` - Verify service integration

### Integration Tests Required
- `test_prometheus_endpoint.py` - Verify metrics export
- `test_alert_integration.py` - Verify alert delivery

### Estimated Commits: 6-8

---

## Milestone 8: Webhook System
**Objective**: Add webhook notifications for job events

### New Interfaces Required
- `src/universal_video_ai/webhook/dispatcher.py` - WebhookDispatcher protocol
- `src/universal_video_ai/webhook/signature.py` - Signature verification protocol

### Files Allowed to Modify
- `src/universal_video_ai/webhook/` - Implement webhook system
- `src/universal_video_ai/jobs/service.py` - Add webhook hooks
- `src/universal_video_ai/database/` - Add webhook storage
- `.env.example` - Add webhook configuration
- `tests/` - Add webhook tests

### Files Forbidden to Modify
- Job data models
- Service layer signatures

### Acceptance Criteria
1. Webhooks registered per user
2. Webhooks triggered on job completion
3. Webhook payload includes job details
4. Signature verification (HMAC)
5. Retry failed webhook deliveries
6. Webhook logging and audit trail
7. Rate limiting per webhook

### Unit Tests Required
- `test_webhook_dispatcher.py` - Verify webhook delivery
- `test_signature_verification.py` - Verify HMAC verification
- `test_webhook_retry.py` - Verify retry logic

### Integration Tests Required
- `test_webhook_e2e.py` - Full webhook flow
- `test_webhook_security.py` - Verify signature validation

### Estimated Commits: 5-7

---

## Milestone 9: Admin API
**Objective**: Add REST API for admin operations

### New Interfaces Required
- `src/universal_video_ai/api/handlers.py` - API handler protocols
- `src/universal_video_ai/api/middleware.py` - Auth middleware protocol

### Files Allowed to Modify
- `src/universal_video_ai/api/` - Implement REST API
- `scripts/run_api.py` - API server script
- `docker-compose.prod.yml` - Add API service
- `.env.example` - Add API configuration
- `tests/` - Add API tests

### Files Forbidden to Modify
- Business logic services
- Database schema (only add API-specific tables if needed)

### Acceptance Criteria
1. GET /api/jobs - List all jobs with filters
2. GET /api/jobs/{id} - Get job details
3. POST /api/jobs - Submit new job
4. DELETE /api/jobs/{id} - Cancel job
5. GET /api/users - List users
6. POST /api/users/{id}/credits - Add credits
7. GET /api/metrics - System metrics
8. API authentication (JWT or API key)
9. Rate limiting on API
10. OpenAPI/Swagger documentation

### Unit Tests Required
- `test_api_handlers.py` - Verify each endpoint
- `test_auth_middleware.py` - Verify authentication
- `test_rate_limiting.py` - Verify rate limiting

### Integration Tests Required
- `test_api_e2e.py` - Full API workflow
- `test_api_security.py` - Verify auth enforcement

### Estimated Commits: 8-10

---

## Milestone 10: Production Hardening
**Objective**: Final production deployment and optimization

### New Interfaces Required
None (infrastructure improvements)

### Files Allowed to Modify
- `Dockerfile` - Optimize for production
- `docker-compose.prod.yml` - Final production config
- `nginx.conf` - Reverse proxy configuration
- `.env.example` - Complete production config
- `scripts/` - Deployment scripts
- `docs/` - Deployment documentation
- `tests/` - Add load tests

### Files Forbidden to Modify
- Core application code (only optimizations)
- Service interfaces

### Acceptance Criteria
1. Docker image size optimized (<2GB)
2. Health checks on all services
3. Graceful shutdown handling
4. Log aggregation configured
5. Database backups automated
6. SSL/TLS termination
7. CDN integration for static files
8. Load testing passes (100 concurrent jobs)
9. Security audit passed
10. Disaster recovery plan documented

### Unit Tests Required
- `test_health_checks.py` - Verify all health endpoints
- `test_graceful_shutdown.py` - Verify clean shutdown

### Integration Tests Required
- `test_load_100_concurrent.py` - Load test
- `test_disaster_recovery.py` - Verify backup/restore

### Estimated Commits: 6-8

---

## Summary

**Total Milestones**: 10
**Estimated Total Commits**: ~60
**Core Architecture**: Frozen and stable
**Flexibility**: Protocol-based design allows backend swaps
**Testing**: Comprehensive unit and integration tests per milestone
**Production Ready**: Monitoring, alerting, webhooks, API included

## Execution Guidelines

1. **Follow milestone order** - Each builds on previous
2. **Test-first approach** - Write tests before implementation
3. **Respect file permissions** - Never modify forbidden files
4. **Document changes** - Update docs with each milestone
5. **Code review** - Each milestone requires review before next
6. **Backward compatibility** - Maintain API compatibility within milestones

## AI Development Rules

When using AI assistants (ChatGPT, Copilot, Claude, etc.):

1. **Provide milestone context** - Always reference current milestone
2. **Show file permissions** - Explicitly state allowed/forbidden files
3. **Require tests** - Insist on unit and integration tests
4. **Review protocol compliance** - Ensure new code follows protocols
5. **No architectural drift** - Reject suggestions that modify core interfaces
6. **Incremental changes** - One milestone at a time

This roadmap ensures consistent architecture regardless of which AI or developer works on the project.
