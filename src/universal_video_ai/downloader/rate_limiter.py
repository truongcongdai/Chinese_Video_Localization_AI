"""Rate limiter for download requests to prevent IP blocking."""
import asyncio
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional


class RateLimiter:
    """Rate limiter using token bucket algorithm with per-user and global limits."""
    
    def __init__(
        self,
        max_requests_per_minute: int = 10,
        max_concurrent: int = 3,
        delay_between_requests: float = 2.0,
    ):
        self.max_requests_per_minute = max(1, max_requests_per_minute)
        self.max_concurrent = max_concurrent
        self.delay_between_requests = delay_between_requests
        
        # Per-user request tracking
        self.user_requests: defaultdict = defaultdict(lambda: deque())
        self.user_locks: defaultdict = defaultdict(Lock)
        
        # Global request tracking
        self.global_requests = deque()
        self.global_lock = Lock()
        
        # Concurrent download tracking
        self.active_downloads = 0
        self.concurrent_lock = Lock()
        self.concurrent_semaphore = asyncio.Semaphore(max_concurrent)
    
    async def acquire(self, user_id: Optional[int] = None) -> None:
        """Wait until a download slot is available and rate limit allows it."""
        # Wait for concurrent slot
        await self.concurrent_semaphore.acquire()
        try:
            await self._wait_for_rate_limit(user_id)
        except BaseException:
            self.concurrent_semaphore.release()
            raise
        with self.concurrent_lock:
            self.active_downloads += 1
    
    def release(self) -> None:
        """Release a download slot."""
        with self.concurrent_lock:
            self.active_downloads -= 1
        self.concurrent_semaphore.release()
    
    async def _wait_for_rate_limit(self, user_id: Optional[int] = None) -> None:
        """Wait until rate limits allow the request."""
        while True:
            # Reserve global and user quota together. No thread lock survives
            # an await; competing requests recheck after waking up.
            with self.global_lock:
                now = time.monotonic()
                queues = [self.global_requests]
                if user_id is not None:
                    queues.append(self.user_requests[user_id])
                wait_time = 0.0
                for queue in queues:
                    while queue and now - queue[0] >= 60:
                        queue.popleft()
                    if len(queue) >= self.max_requests_per_minute:
                        wait_time = max(wait_time, 60 - (now - queue[0]))
                if wait_time <= 0:
                    for queue in queues:
                        queue.append(now)
                    break
            await asyncio.sleep(wait_time)
        
        # Add delay between requests
        if self.delay_between_requests > 0:
            await asyncio.sleep(self.delay_between_requests)
    
    def get_stats(self) -> dict:
        """Get current rate limiter statistics."""
        now = time.monotonic()
        
        with self.global_lock:
            global_count = len([t for t in self.global_requests if now - t <= 60])
        
        return {
            "global_requests_last_minute": global_count,
            "max_requests_per_minute": self.max_requests_per_minute,
            "max_concurrent": self.max_concurrent,
            "active_downloads": self.active_downloads,
        }


# Global rate limiter instance
_global_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the global rate limiter instance."""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = RateLimiter()
    return _global_rate_limiter


def configure_rate_limiter(
    max_requests_per_minute: int = 10,
    max_concurrent: int = 3,
    delay_between_requests: float = 2.0,
) -> None:
    """Configure the global rate limiter."""
    global _global_rate_limiter
    _global_rate_limiter = RateLimiter(
        max_requests_per_minute=max_requests_per_minute,
        max_concurrent=max_concurrent,
        delay_between_requests=delay_between_requests,
    )
