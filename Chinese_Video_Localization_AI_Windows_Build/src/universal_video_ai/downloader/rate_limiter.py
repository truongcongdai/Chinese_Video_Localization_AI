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
        self.max_requests_per_minute = max_requests_per_minute
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
        
        # Apply rate limiting
        await self._wait_for_rate_limit(user_id)
    
    def release(self) -> None:
        """Release a download slot."""
        self.concurrent_semaphore.release()
    
    async def _wait_for_rate_limit(self, user_id: Optional[int] = None) -> None:
        """Wait until rate limits allow the request."""
        now = time.time()
        
        # Check global rate limit
        with self.global_lock:
            # Remove requests older than 1 minute
            while self.global_requests and now - self.global_requests[0] > 60:
                self.global_requests.popleft()
            
            # If at limit, wait
            if len(self.global_requests) >= self.max_requests_per_minute:
                wait_time = 60 - (now - self.global_requests[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    now = time.time()
            
            # Add current request
            self.global_requests.append(now)
        
        # Check per-user rate limit
        if user_id is not None:
           with self.user_locks[user_id]:
                user_queue = self.user_requests[user_id]
                
                # Remove old requests
                while user_queue and now - user_queue[0] > 60:
                    user_queue.popleft()
                
                # If at limit, wait
                if len(user_queue) >= self.max_requests_per_minute:
                    wait_time = 60 - (now - user_queue[0])
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                        now = time.time()
                
                # Add current request
                user_queue.append(now)
        
        # Add delay between requests
        if self.delay_between_requests > 0:
            await asyncio.sleep(self.delay_between_requests)
    
    def get_stats(self) -> dict:
        """Get current rate limiter statistics."""
        now = time.time()
        
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
