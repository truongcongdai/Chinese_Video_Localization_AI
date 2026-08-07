"""
Trend Scanner service for discovering trending videos.

This service coordinates multiple trend providers (including optional Agent-Reach)
to discover trending content across platforms.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from universal_video_ai.trends.models import TrendItemCandidate, TrendScanResult
from universal_video_ai.trends.providers.base import BaseTrendProvider
from universal_video_ai.trends.providers.agent_reach import AgentReachProvider
from universal_video_ai.trends.scoring import calculate_trend_score, normalize_scores

_logger = logging.getLogger(__name__)


class TrendScanner:
    """Main service for scanning trending videos across platforms."""

    def __init__(self, logger=None):
        self.logger = logger or _logger
        self._providers: Dict[str, BaseTrendProvider] = {}
        self._register_default_providers()

    def _register_default_providers(self):
        """Register available trend providers."""
        # Register Agent-Reach as optional provider
        agent_reach = AgentReachProvider(logger=self.logger)
        if agent_reach.is_available():
            self._providers["agent_reach"] = agent_reach
            self.logger.info("Agent-Reach provider registered and available")
        else:
            self.logger.info("Agent-Reach provider not available (disabled or not installed)")

    def register_provider(self, name: str, provider: BaseTrendProvider):
        """Register a custom trend provider."""
        self._providers[name] = provider
        self.logger.info("Registered custom provider: %s", name)

    def scan(
        self,
        topic: str,
        platforms: Optional[List[str]] = None,
        max_results: int = 20,
        use_agent_reach_fallback: bool = False,
    ) -> TrendScanResult:
        """
        Scan for trending content across platforms.

        Args:
            topic: Search topic or query
            platforms: List of platforms to search (e.g., ["youtube", "tiktok"])
            max_results: Maximum number of results to return
            use_agent_reach_fallback: Whether to use Agent-Reach as fallback

        Returns:
            TrendScanResult with discovered items and any warnings
        """
        scan_id = str(uuid.uuid4())
        warnings: List[str] = []
        all_candidates: List[TrendItemCandidate] = []

        effective_platforms = platforms or []

        # Check if Agent-Reach should be used
        if use_agent_reach_fallback and "agent_reach" in self._providers:
            agent_reach = self._providers["agent_reach"]
            try:
                candidates = agent_reach.search(topic, max_results, effective_platforms)
                all_candidates.extend(candidates)
                if candidates:
                    self.logger.info(
                        "Agent-Reach found %d candidates for topic '%s'", len(candidates), topic
                    )
                else:
                    warnings.append("Agent-Reach returned no results")
            except Exception as exc:
                self.logger.warning("Agent-Reach search failed: %s", exc)
                warnings.append(f"Agent-Reach search failed: {exc}")
        elif use_agent_reach_fallback and "agent_reach" not in self._providers:
            warnings.append("Agent-Reach fallback requested but provider not available")

        # If no candidates found and Agent-Reach wasn't used or failed
        if not all_candidates and not use_agent_reach_fallback:
            warnings.append("No native providers available for the requested platforms")

        # Calculate and normalize trend scores
        if all_candidates:
            # Calculate scores based on engagement metrics
            for item in all_candidates:
                if item.trend_score == 0:
                    item.trend_score = calculate_trend_score(item)

            # Normalize scores to 0-1 range
            all_candidates = normalize_scores(all_candidates)

            # Sort by trend score (descending)
            all_candidates.sort(key=lambda x: x.trend_score, reverse=True)

        # Limit to max_results
        all_candidates = all_candidates[:max_results]

        result = TrendScanResult(
            scan_id=scan_id,
            topic=topic,
            platforms=effective_platforms,
            items=all_candidates,
            warnings=warnings,
            max_results=max_results,
        )

        self.logger.info(
            "Trend scan '%s' completed: %d items found for topic '%s'",
            scan_id,
            len(all_candidates),
            topic,
        )

        return result

    def get_available_providers(self) -> List[str]:
        """Return list of available provider names."""
        return list(self._providers.keys())

    def is_provider_available(self, name: str) -> bool:
        """Check if a specific provider is available."""
        return name in self._providers
