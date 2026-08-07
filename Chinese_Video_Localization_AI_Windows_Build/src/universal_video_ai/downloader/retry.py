# src/universal_video_ai/downloader/retry.py
from __future__ import annotations

from dataclasses import dataclass
import logging
import random
import time
from typing import Callable, Generic, Optional, Tuple, Type, TypeVar

T = TypeVar("T")

__all__ = ["RetryPolicy", "RetryExecutor", "retry"]

DEFAULT_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """
    Configuration for retry behavior.

    Attributes:
        max_attempts: total attempts including the first try (must be >=1).
        base_backoff_seconds: initial backoff (seconds) used for attempt 2.
        max_backoff_seconds: maximum backoff seconds.
        backoff_multiplier: backoff growth factor (exponential).
        retry_on_exceptions: exception types that should trigger a retry.
        jitter: whether to apply random jitter (±10%) to backoff.
    """

    max_attempts: int = 3
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    retry_on_exceptions: Tuple[Type[BaseException], ...] = (Exception,)
    jitter: bool = True


class RetryExecutor(Generic[T]):
    """
    Executor that runs a callable with retries according to a RetryPolicy.

    Usage:
        policy = RetryPolicy(max_attempts=5)
        executor = RetryExecutor(policy)
        result = executor.run(lambda: do_something())

    Notes:
        - This executor is synchronous and uses time.sleep for backoff.
        - Callers may pass a custom logger for structured logging / metrics.
    """

    def __init__(self, policy: Optional[RetryPolicy] = None, logger: Optional[logging.Logger] = None) -> None:
        self.policy: RetryPolicy = policy or RetryPolicy()
        self.logger: logging.Logger = logger or DEFAULT_LOGGER
        self.logger.debug("RetryExecutor initialized with policy=%s", self.policy)

    def _compute_backoff(self, attempt: int) -> float:
        """
        Compute backoff before the given attempt number (1-based).
        Attempt 1 -> no backoff (first immediate try).
        Attempt 2 -> base_backoff_seconds
        Attempt n -> base_backoff_seconds * multiplier^(n-2), capped by max_backoff_seconds.
        """
        if attempt <= 1:
            return 0.0

        exponent = max(0, attempt - 2)
        backoff = self.policy.base_backoff_seconds * (self.policy.backoff_multiplier ** exponent)
        if self.policy.jitter:
            jitter_fraction = 0.1
            jitter_amount = backoff * jitter_fraction
            backoff = backoff + random.uniform(-jitter_amount, jitter_amount)
        backoff = max(0.0, min(backoff, self.policy.max_backoff_seconds))
        self.logger.debug("Computed backoff for attempt %d: %.3f", attempt, backoff)
        return backoff

    def run(
        self,
        func: Callable[..., T],
        *args,
        on_retry: Optional[Callable[[BaseException, int, float], None]] = None,
        **kwargs,
    ) -> T:
        """
        Run callable with retries.

        Parameters:
            func: the callable to run.
            *args, **kwargs: passed to the callable.
            on_retry: optional callback called before sleeping on a retry attempt.
                      Signature: (exception, attempt_number, wait_seconds).
                      attempt_number is 1-based (1..max_attempts).
        Returns:
            The callable's returned value.
        Raises:
            The last raised exception if all attempts fail or raised exception
            not in policy.retry_on_exceptions.
        """
        last_exc: Optional[BaseException] = None

        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                self.logger.debug("Attempt %d/%d running %s", attempt, self.policy.max_attempts, getattr(func, "__name__", repr(func)))
                return func(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - behavior tested via tests
                last_exc = exc
                should_retry = isinstance(exc, self.policy.retry_on_exceptions)
                self.logger.debug("Attempt %d raised %s; should_retry=%s", attempt, type(exc).__name__, should_retry)

                if not should_retry:
                    self.logger.debug("Not retrying because exception type %s not in retry_on_exceptions", type(exc).__name__)
                    raise

                if attempt >= self.policy.max_attempts:
                    self.logger.warning(
                        "All %d attempts failed for %s; raising last exception: %s",
                        self.policy.max_attempts,
                        getattr(func, "__name__", repr(func)),
                        exc,
                    )
                    # Final raise preserving traceback
                    raise

                wait = self._compute_backoff(attempt + 1)
                if on_retry:
                    try:
                        on_retry(exc, attempt, wait)
                    except Exception:
                        self.logger.exception("on_retry callback raised an exception; ignoring")

                self.logger.info(
                    "Retrying %s after %.3fs (attempt %d/%d) due to %s: %s",
                    getattr(func, "__name__", repr(func)),
                    wait,
                    attempt + 1,
                    self.policy.max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if wait > 0:
                    time.sleep(wait)

        # If for some reason loop exits without returning/raising, raise last exception
        if last_exc:
            raise last_exc  # pragma: no cover - defensive
        raise RuntimeError("RetryExecutor.run reached unexpected state")  # pragma: no cover - defensive


def retry(policy: Optional[RetryPolicy] = None):
    """
    Decorator factory that applies retry behavior to a function (synchronous).

    Usage:
        @retry(RetryPolicy(max_attempts=4))
        def work():
            ...
    """
    policy = policy or RetryPolicy()

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args, **kwargs) -> T:
            executor = RetryExecutor(policy=policy)
            return executor.run(lambda: fn(*args, **kwargs))
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator