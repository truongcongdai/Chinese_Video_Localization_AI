# tests/test_retry.py
from typing import List

import pytest

from universal_video_ai.downloader.retry import RetryExecutor, RetryPolicy, retry


def test_success_without_retry(monkeypatch):
    called: List[int] = []

    def work():
        called.append(1)
        return "ok"

    policy = RetryPolicy(max_attempts=3)
    executor = RetryExecutor(policy=policy)
    # patch sleep to ensure no actual sleeping
    monkeypatch.setattr("time.sleep", lambda s: None)
    result = executor.run(work)
    assert result == "ok"
    assert len(called) == 1


def test_retry_then_success(monkeypatch):
    attempts: List[int] = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("transient")
        return "done"

    monkeypatch.setattr("time.sleep", lambda s: None)
    policy = RetryPolicy(max_attempts=5, base_backoff_seconds=0.01, jitter=False, retry_on_exceptions=(ValueError,))
    executor = RetryExecutor(policy=policy)
    result = executor.run(flaky)
    assert result == "done"
    assert len(attempts) == 3


def test_exceed_max_attempts(monkeypatch):
    attempts: List[int] = []

    def always_fail():
        attempts.append(1)
        raise RuntimeError("fatal")

    monkeypatch.setattr("time.sleep", lambda s: None)
    policy = RetryPolicy(max_attempts=3, retry_on_exceptions=(RuntimeError,))
    executor = RetryExecutor(policy=policy)
    with pytest.raises(RuntimeError):
        executor.run(always_fail)
    assert len(attempts) == 3


def test_decorator(monkeypatch):
    calls: List[int] = []

    @retry(RetryPolicy(max_attempts=2, base_backoff_seconds=0.01, jitter=False, retry_on_exceptions=(ValueError,)))
    def func():
        calls.append(1)
        if len(calls) < 2:
            raise ValueError("transient")
        return "ok"

    monkeypatch.setattr("time.sleep", lambda s: None)
    assert func() == "ok"
    assert len(calls) == 2