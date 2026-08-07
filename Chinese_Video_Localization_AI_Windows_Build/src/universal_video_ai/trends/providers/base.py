"""
Base class for trend providers.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from universal_video_ai.trends.models import TrendItemCandidate


class BaseTrendProvider(ABC):
    """Abstract base class for trend providers."""

    def __init__(self, logger=None):
        self.logger = logger

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is available and configured."""
        pass

    @abstractmethod
    def search(
        self,
        topic: str,
        max_results: int = 20,
        channels: Optional[List[str]] = None,
    ) -> List[TrendItemCandidate]:
        """
        Search for trending content.

        Args:
            topic: Search topic or query
            max_results: Maximum number of results to return
            channels: List of platform-specific channels to search (optional)

        Returns:
            List of TrendItemCandidate objects
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass
