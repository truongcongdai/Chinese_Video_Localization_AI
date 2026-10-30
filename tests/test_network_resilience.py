"""Retries and capacity must stay bounded when providers stall or fail."""
from __future__ import annotations

import asyncio
import threading

import pytest
import requests

from universal_video_ai.analytics.youtube_research.collector import (
    YtDlpYouTubeResearchCollector, YouTubeCollectorTimeoutError,
)
from universal_video_ai.cache.redis_cache import RedisCache
from universal_video_ai.network import NetworkRetryPolicy, post_with_retry
from universal_video_ai.content_os.agents.content_audit_agent import ContentAuditAgent
from universal_video_ai.content_os.agents.llm_router import LLMRouter
from universal_video_ai.content_os.exceptions import LLMOutputError


def test_retry_transient_response_and_honor_retry_after(monkeypatch) -> None:
    first = requests.Response()
    first.status_code = 429
    first.headers["Retry-After"] = "2"
    second = requests.Response()
    second.status_code = 200
    attempts = []
    delays = []
    closed = []
    monkeypatch.setattr(first, "close", lambda: closed.append(True))
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: attempts.append(kwargs) or [first, second][len(attempts) - 1])
    monkeypatch.setattr("universal_video_ai.network.time.sleep", delays.append)
    response = post_with_retry("https://example.test/generate", policy=NetworkRetryPolicy(max_delay=3))
    assert response is second
    assert len(attempts) == 2 and delays == [2.0]
    assert closed == [True]


def test_retry_network_timeout_but_not_client_error(monkeypatch) -> None:
    attempts = []
    def timeout(*args, **kwargs):
        attempts.append(1)
        raise requests.Timeout("temporary")
    monkeypatch.setattr(requests, "post", timeout)
    monkeypatch.setattr("universal_video_ai.network.time.sleep", lambda _: None)
    with pytest.raises(requests.Timeout):
        post_with_retry("https://example.test/generate", policy=NetworkRetryPolicy(max_attempts=3))
    assert len(attempts) == 3

    bad = requests.Response()
    bad.status_code = 400
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: bad)
    assert post_with_retry("https://example.test/generate") is bad


def test_mock_llm_never_uses_http_even_with_a_key(monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_EXECUTION_MODE", "MOCK")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("MOCK sent HTTP"))
    result = LLMRouter(provider="gemini").invoke("audit this", output_schema=object)
    assert "raw_content" in result


def test_invalid_retry_after_does_not_break_retry(monkeypatch) -> None:
    first = requests.Response()
    first.status_code = 503
    first._content = b"busy"
    first._content_consumed = True
    first.headers["Retry-After"] = "NaN"
    second = requests.Response()
    second.status_code = 200
    responses = iter((first, second))
    delays = []
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("universal_video_ai.network.time.sleep", delays.append)
    assert post_with_retry("https://example.test/generate") is second
    assert delays == [0.0]


@pytest.mark.parametrize("failure", [requests.ConnectionError, requests.exceptions.ChunkedEncodingError])
def test_retry_transport_failure_recovers(monkeypatch, failure) -> None:
    response = requests.Response()
    response.status_code = 200
    attempts = []

    def post(*args, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise failure("connection interrupted")
        return response

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("universal_video_ai.network.time.sleep", lambda _: None)
    assert post_with_retry("https://example.test/generate") is response
    assert len(attempts) == 2
    assert attempts[0]["timeout"] == (10.0, 120.0)


def test_certificate_failure_is_not_retried(monkeypatch) -> None:
    attempts = []

    def post(*args, **kwargs):
        attempts.append(1)
        raise requests.exceptions.SSLError("invalid certificate")

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(requests.exceptions.SSLError):
        post_with_retry("https://example.test/generate")
    assert len(attempts) == 1


@pytest.mark.parametrize("options", [{"read_timeout": float("inf")}, {"base_delay": float("nan")}, {"max_attempts": 1.5}])
def test_retry_rejects_unbounded_policy(options) -> None:
    with pytest.raises(ValueError):
        NetworkRetryPolicy(**options)


def test_collector_timeout_keeps_its_slot_until_extractor_exits(monkeypatch) -> None:
    collector = YtDlpYouTubeResearchCollector(hard_max_results=2, timeout_seconds=1)
    collector.timeout_seconds = 0.02
    monkeypatch.setattr(collector, "is_available", lambda: True)
    release = threading.Event()
    started = threading.Event()
    calls = []

    def extract(query, limit):
        calls.append(query)
        started.set()
        if query == "first":
            release.wait(timeout=2)
        return {"entries": []}

    monkeypatch.setattr(collector, "_extract_sync", extract)

    async def scenario():
        with pytest.raises(YouTubeCollectorTimeoutError):
            await collector.search("first", 1)
        assert started.is_set()
        collector.timeout_seconds = 0.2
        second = asyncio.create_task(collector.search("second", 1))
        await asyncio.sleep(0.01)
        assert calls == ["first"]
        release.set()
        assert await second == []
        assert calls == ["first", "second"]

    asyncio.run(scenario())


def test_memory_fallback_has_a_fixed_capacity() -> None:
    cache = RedisCache(url="redis://127.0.0.1:1/0", max_memory_entries=2)
    cache.set("first", "one")
    cache.set("second", "two")
    assert cache.get("first") == "one"
    cache.set("third", "three")
    assert cache.get("second") is None
    assert cache.get("first") == "one"


def test_malformed_blocking_audit_cannot_become_pass() -> None:
    audit = ContentAuditAgent()
    with pytest.raises(LLMOutputError):
        audit.validate_output({
            "decision": "BLOCKED",
            "issues": [{"severity": "critical", "description": "unsafe"}],
        })
