# Universal Video AI - Testing Guide

## Purpose
This document defines testing standards, strategies, and requirements for Universal Video AI. All code (human and AI-generated) MUST meet these standards.

## Testing Philosophy

### Test Pyramid
```
        E2E Tests (5%)
       /            \
    Integration Tests (15%)
   /                  \
Unit Tests (80%)
```

- **Unit Tests**: Fast, isolated, test single functions/classes
- **Integration Tests**: Test module interactions
- **E2E Tests**: Test full workflows (rare, expensive)

### Test-First Development
Write tests BEFORE implementation:
1. Write failing test
2. Write minimal code to pass test
3. Refactor
4. Repeat

## Unit Testing Standards

### Test Structure
Every test must follow Arrange-Act-Assert pattern:

```python
def test_function_name_scenario_expected_result():
    # Arrange
    input_data = create_test_data()
    expected_output = "expected"

    # Act
    result = function_under_test(input_data)

    # Assert
    assert result == expected_output
```

### Test Naming
Use descriptive names: `test_{function}_{scenario}_{expected}`

```python
# ✅ CORRECT
def test_download_success_returns_video_path():
    pass

def test_download_invalid_url_raises_error():
    pass

def test_download_network_failure_returns_failure_result():
    pass

# ❌ AVOID
def test_download_1():
    pass

def test_download_test():
    pass
```

### Test Organization
Group tests by class/module:

```python
class TestDownloadService:
    def test_download_success(self):
        pass

    def test_download_invalid_url(self):
        pass

    def test_download_network_error(self):
        pass
```

### Fixtures
Use pytest fixtures for common setup:

```python
@pytest.fixture
def sample_video_url():
    return "https://example.com/video.mp4"

@pytest.fixture
def mock_downloader():
    return MockDownloader()

def test_download_with_fixture(sample_video_url, mock_downloader):
    result = mock_downloader.download(sample_video_url)
    assert result.success
```

### Mocking
Mock external dependencies:

```python
from unittest.mock import Mock, patch

def test_transcribe_with_mock():
    # Arrange
    mock_backend = Mock()
    mock_backend.transcribe.return_value = "test transcript"
    service = SpeechService(backend=mock_backend)

    # Act
    result = service.transcribe(Path("/tmp/audio.wav"))

    # Assert
    assert result == "test transcript"
    mock_backend.transcribe.assert_called_once()
```

### Testing Exceptions
Test exception handling:

```python
def test_invalid_url_raises_value_error():
    with pytest.raises(ValueError) as exc_info:
        validate_url("not-a-url")
    assert "must start with" in str(exc_info.value)
```

### Parameterized Tests
Use pytest.mark.parametrize for similar tests:

```python
@pytest.mark.parametrize("url,expected", [
    ("https://example.com", True),
    ("http://example.com", True),
    ("ftp://example.com", False),
    ("not-a-url", False),
])
def test_url_validation(url, expected):
    assert is_valid_url(url) == expected
```

## Integration Testing Standards

### Test Module Interactions
Test how modules work together:

```python
def test_localization_pipeline_integration():
    # Arrange
    downloader = DownloadService()
    speech_service = SpeechService(backend=MockBackend())
    orchestrator = LocalizationService(
        downloader=downloader,
        speech_service=speech_service
    )

    # Act
    result = orchestrator.localize(
        url="https://example.com/video.mp4",
        output_dir=Path("/tmp/output")
    )

    # Assert
    assert result.download_result.success
    assert result.audio_pipeline_result.transcript is not None
```

### Test Database Interactions
Test database operations:

```python
def test_user_credit_deduction():
    # Arrange
    db = DatabaseManager(":memory:")
    db.init_schema()
    db.add_credits(user_id=123, amount=10.0)

    # Act
    success = db.deduct_credits(user_id=123, amount=1.0)

    # Assert
    assert success
    credits = db.get_user_credits(user_id=123)
    assert credits.credits == 9.0
```

### Test API Interactions
Test external API calls with mocking:

```python
def test_translation_with_real_api_mock():
    with patch('googletrans.Translator') as mock_translator:
        # Arrange
        mock_translator.return_value.translate.return_value.text = "Xin chào"
        service = TranslateService(backend=TranslatorBackend())

        # Act
        result = service.translate("Hello", "en", "vi")

        # Assert
        assert result == "Xin chào"
```

## Test Coverage Requirements

### Coverage Targets
- **Overall coverage**: >80%
- **Core modules** (services): >90%
- **Business logic**: >95%
- **Utilities**: >70%

### Coverage Tools
```bash
# Generate coverage report
pytest --cov=src/universal_video_ai --cov-report=html

# Check coverage threshold
pytest --cov=src/universal_video_ai --cov-fail-under=80
```

### Coverage Exclusions
```python
# .coveragerc
[run]
omit =
    */__main__.py
    */tests/*
    */venv/*
    */.venv/*
```

## Test Data Management

### Test Fixtures Directory
```
tests/
├── fixtures/
│   ├── audio/
│   │   ├── sample_chinese.wav
│   │   └── sample_english.wav
│   ├── video/
│   │   └── sample.mp4
│   └── config/
│       └── test_config.yaml
```

### Temporary Files
Use pytest tmp_path fixture:

```python
def test_with_temp_file(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")
    assert test_file.exists()
```

### Test Configuration
Use separate test config:

```python
@pytest.fixture
def test_config():
    return Config(
        temp_dir=Path("/tmp/test"),
        database_path=":memory:",
        log_level="DEBUG"
    )
```

## Async Testing

### Testing Async Code
Use pytest-asyncio:

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result == "expected"
```

## Performance Testing

### Benchmark Tests
Use pytest-benchmark:

```python
def test_transcribe_performance(benchmark):
    service = SpeechService(backend=MockBackend())
    result = benchmark(service.transcribe, Path("/tmp/audio.wav"))
    assert result is not None
```

## Property-Based Testing

### Use Hypothesis
Test with random inputs:

```python
from hypothesis import given, strategies as st

@given(st.text())
def test_translation_roundtrip(text):
    # Test that translation doesn't crash on any text
    result = translate(text, "en", "vi")
    assert isinstance(result, str)
```

## Test Documentation

### Docstrings in Tests
Document complex tests:

```python
def test_demucs_separation_with_vocals():
    """
    Test that Demucs successfully separates audio into vocals and background.
    
    This test uses a sample audio file with known vocal content and verifies
    that the separated vocal track contains the expected frequency range.
    
    Regression test for issue #123 where vocal separation was failing.
    """
    # Test implementation
    pass
```

## Continuous Integration

### CI Test Pipeline
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run tests
        run: pytest --cov=src/universal_video_ai --cov-fail-under=80
```

## Test-Specific Milestone Requirements

### Milestone 1 (Dummy Backend)
**Required Tests**:
- `test_dummy_speech_backend.py` - NoOpSpeechBackend returns placeholder
- `test_dummy_translate_backend.py` - NoOpTranslateBackend returns input
- `test_dummy_tts_backend.py` - NoOpTTSBackend creates placeholder file
- `test_orchestrator_factory.py` - Factory creates correct backends
- `test_localize_command_dummy.py` - Full /localize command flow

### Milestone 2 (Whisper)
**Required Tests**:
- `test_whisper_transcriber.py` - Mock whisper.load_model
- `test_whisper_backend.py` - Backend delegates correctly
- `test_whisper_config.py` - Configuration parsing
- `test_transcribe_chinese_audio.py` - Real audio transcription
- `test_whisper_error_handling.py` - Model load failure handling

### Milestone 3 (Translation)
**Required Tests**:
- `test_google_translator.py` - Mock GoogleTrans API
- `test_translate_backend.py` - Error conversion
- `test_translation_cache.py` - Caching logic
- `test_translate_chinese_to_vietnamese.py` - Real API call
- `test_translation_error_recovery.py` - API failure fallback

### Milestone 4 (TTS)
**Required Tests**:
- `test_edge_tts.py` - Mock edge-tts subprocess
- `test_tts_backend.py` - Error handling
- `test_tts_config.py` - Voice configuration
- `test_synthesize_vietnamese.py` - Real EdgeTTS call
- `test_tts_audio_quality.py` - Output audio properties

### Milestone 5 (Demucs)
**Required Tests**:
- `test_demucs_processor.py` - Mock demucs subprocess
- `test_demucs_config.py` - Configuration parsing
- `test_audio_pipeline_demucs.py` - Pipeline integration
- `test_demucs_separation.py` - Real Demucs call
- `test_demucs_fallback.py` - Unavailable fallback

### Milestone 6 (Job Queue)
**Required Tests**:
- `test_job_queue.py` - Redis queue operations
- `test_job_worker.py` - Worker processing logic
- `test_retry_logic.py` - Retry with backoff
- `test_queue_integration.py` - Full queue → worker flow
- `test_multi_worker.py` - Multiple workers
- `test_job_cancellation.py` - Cancel in-flight jobs

### Milestone 7 (Monitoring)
**Required Tests**:
- `test_metrics_collector.py` - Metric recording
- `test_alert_manager.py` - Alert triggering
- `test_metrics_hooks.py` - Service integration
- `test_prometheus_endpoint.py` - Metrics export
- `test_alert_integration.py` - Alert delivery

### Milestone 8 (Webhook)
**Required Tests**:
- `test_webhook_dispatcher.py` - Webhook delivery
- `test_signature_verification.py` - HMAC verification
- `test_webhook_retry.py` - Retry logic
- `test_webhook_e2e.py` - Full webhook flow
- `test_webhook_security.py` - Signature validation

### Milestone 9 (Admin API)
**Required Tests**:
- `test_api_handlers.py` - Each endpoint
- `test_auth_middleware.py` - Authentication
- `test_rate_limiting.py` - Rate limiting
- `test_api_e2e.py` - Full API workflow
- `test_api_security.py` - Auth enforcement

### Milestone 10 (Production)
**Required Tests**:
- `test_health_checks.py` - Health endpoints
- `test_graceful_shutdown.py` - Clean shutdown
- `test_load_100_concurrent.py` - Load test
- `test_disaster_recovery.py` - Backup/restore

## Anti-Patterns

### ❌ Testing Implementation Details
```python
# BAD - Tests internal implementation
def test_service_calls_private_method():
    service = Service()
    assert service._internal_method() == "result"

# GOOD - Tests public behavior
def test_service_returns_expected_result():
    service = Service()
    assert service.process() == "result"
```

### ❌ Fragile Tests
```python
# BAD - Tests exact output format
def test_output_format():
    assert str(result) == "Result: value at 2024-01-01"

# GOOD - Tests key information
def test_output_contains_key_info():
    assert "value" in str(result)
    assert "Result:" in str(result)
```

### ❌ Test Interdependence
```python
# BAD - Tests depend on execution order
def test_first():
    global state
    state = "initialized"

def test_second():
    assert state == "initialized"  # Depends on first test

# GOOD - Each test is independent
def test_first():
    state = initialize_state()
    assert state == "initialized"

def test_second():
    state = initialize_state()  # Independent setup
    assert state == "initialized"
```

## Test Maintenance

### Regular Test Review
- Review tests monthly for relevance
- Remove obsolete tests
- Update tests for new features
- Refactor duplicated test code

### Test Documentation
Keep test documentation updated:
- Document complex test scenarios
- Document test data sources
- Document test environment requirements

## Enforcement

Testing standards are enforced through:
1. Pre-commit hooks (pytest runs on commit)
2. CI/CD pipeline (tests must pass)
3. Code review (check test coverage)
4. Coverage thresholds (fail if below 80%)

This testing guide ensures code quality, reliability, and maintainability.
