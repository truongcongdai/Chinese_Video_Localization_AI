"""Bounded retries for repeatable provider requests, never remote uploads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import random
import time
from typing import Any

import requests

__all__ = ["NetworkRetryPolicy", "post_with_retry"]

_logger = logging.getLogger(__name__)
_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class NetworkRetryPolicy:
    """Total attempts, bounded backoff, and finite connect/read deadlines."""

    max_attempts: int = 3
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    base_delay: float = 0.5
    max_delay: float = 15.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("Network attempts must be a positive integer")
        if not all(math.isfinite(value) for value in (
            self.connect_timeout, self.read_timeout, self.base_delay, self.max_delay,
        )):
            raise ValueError("Network deadlines and backoff must be finite")
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("Network attempts and timeouts must be positive")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("Network backoff cannot be negative")


def _retry_delay(response: Any, policy: NetworkRetryPolicy, attempt: int) -> float:
    value = (getattr(response, "headers", None) or {}).get("Retry-After")
    if value is not None:
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                when = parsedate_to_datetime(str(value))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                delay = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = 0.0
        return max(0.0, min(policy.max_delay, delay)) if math.isfinite(delay) else 0.0
    cap = min(policy.max_delay, policy.base_delay * (2 ** min(attempt, 30)))
    return random.uniform(0.0, cap)


def post_with_retry(
    url: str, *, policy: NetworkRetryPolicy | None = None,
    retry_category: str = "llm_calls", **kwargs: Any
) -> requests.Response:
    """POST a repeatable generation request with transient-error retries.

    Caller explicitly opts into retrying this operation by using this helper.
    Do not use for publishing or OAuth code exchanges. No URL, request body,
    credential, or provider error body is logged. Successful/final responses
    belong to the caller; discarded retry responses are closed here.
    """
    policy = policy or NetworkRetryPolicy()
    kwargs.setdefault("timeout", (policy.connect_timeout, policy.read_timeout))
    for attempt in range(policy.max_attempts):
        if attempt:
            # The caller reserves the first operation; retries are new remote
            # attempts and must consume the active automation budget as well.
            from universal_video_ai.channel_agent.coordination import reserve_provider_call
            reserve_provider_call(retry_category)
        response = None
        try:
            response = requests.post(url, **kwargs)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
            if isinstance(exc, requests.exceptions.SSLError) or attempt + 1 == policy.max_attempts:
                raise
            reason = type(exc).__name__
        else:
            if response.status_code not in _TRANSIENT_STATUSES or attempt + 1 == policy.max_attempts:
                return response
            reason = f"HTTP {response.status_code}"
        delay = _retry_delay(response, policy, attempt)
        if response is not None:
            response.close()
        _logger.warning(
            "Transient provider failure (%s); retry %d/%d in %.2fs",
            reason, attempt + 2, policy.max_attempts, delay,
        )
        time.sleep(delay)
    raise RuntimeError("Network retry loop exited unexpectedly")
