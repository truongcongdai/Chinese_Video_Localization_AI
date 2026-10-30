"""
Agent-Reach provider for Trend Scanner.

This provider uses Agent-Reach as an optional fallback for discovering trending content
from supported platforms. It is disabled by default and requires explicit configuration.
"""

import logging
import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from universal_video_ai.trends.providers.base import BaseTrendProvider
from universal_video_ai.trends.models import TrendItemCandidate

_logger = logging.getLogger(__name__)


class AgentReachProvider(BaseTrendProvider):
    """Agent-Reach trend provider - optional fallback for platform discovery."""

    def __init__(self, logger=None):
        super().__init__(logger or _logger)
        self._command = os.getenv("AGENT_REACH_COMMAND", "agent-reach")
        self._timeout = int(os.getenv("AGENT_REACH_TIMEOUT_SECONDS", "30"))
        self._max_results = int(os.getenv("AGENT_REACH_MAX_RESULTS", "20"))
        self._enabled = os.getenv("AGENT_REACH_ENABLED", "false").lower() in ("true", "1", "yes")

    @property
    def provider_name(self) -> str:
        return "agent_reach"

    def is_available(self) -> bool:
        """Check if Agent-Reach is available and enabled."""
        if not self._enabled:
            self.logger.info("Agent-Reach provider is disabled via AGENT_REACH_ENABLED")
            return False

        if not shutil.which(self._command):
            self.logger.warning(
                "Agent-Reach command '%s' not found in PATH", self._command
            )
            return False

        # Optionally run doctor to check installation
        try:
            result = self._run_agent_reach_command(["doctor"], timeout=10)
            if result["ok"]:
                self.logger.info("Agent-Reach doctor check passed")
                return True
            else:
                self.logger.warning(
                    "Agent-Reach doctor check failed: %s", result.get("stderr", "unknown error")
                )
                return False
        except Exception as exc:
            self.logger.warning("Agent-Reach availability check failed: %s", exc)
            return False

    def search(
        self,
        topic: str,
        max_results: int = 20,
        channels: Optional[List[str]] = None,
    ) -> List[TrendItemCandidate]:
        """
        Search for trending content using Agent-Reach.

        For MVP, this implements a defensive approach:
        - Tries supported free/safe channels first (youtube, rss/web, bilibili)
        - Reddit/Twitter/Xiaohongshu only if explicitly configured
        - Does NOT assume Douyin/Kuaishou/TikTok support unless doctor confirms
        - Returns partial results if parsing fails
        """
        if not self.is_available():
            return []

        effective_max = min(max_results, self._max_results)
        all_candidates: List[TrendItemCandidate] = []

        # Default safe channels to try
        safe_channels = ["youtube", "rss", "web"]
        if channels:
            # Only include channels that are safe/known to work
            safe_channels = [c for c in channels if c in safe_channels]

        for channel in safe_channels:
            try:
                candidates = self._search_channel(topic, channel, effective_max)
                all_candidates.extend(candidates)
            except Exception as exc:
                self.logger.warning(
                    "Agent-Reach search failed for channel '%s': %s", channel, exc
                )

        # Deduplicate by platform + source_url
        seen = set()
        unique_candidates = []
        for candidate in all_candidates:
            key = (candidate.platform, candidate.source_url)
            if key not in seen:
                seen.add(key)
                unique_candidates.append(candidate)

        return unique_candidates[:effective_max]

    def _search_channel(
        self, topic: str, channel: str, max_results: int
    ) -> List[TrendItemCandidate]:
        """Search a specific channel via Agent-Reach."""
        # TODO: Adjust command syntax based on actual Agent-Reach doctor output
        # This is a placeholder implementation that will need refinement
        # based on the actual Agent-Reach CLI interface

        args = ["search", "--platform", channel, "--query", topic, "--limit", str(max_results)]
        result = self._run_agent_reach_command(args, timeout=self._timeout)

        if not result["ok"]:
            self.logger.warning(
                "Agent-Reach command failed for channel '%s': %s",
                channel,
                result.get("stderr", "unknown error"),
            )
            return []

        # Structured output only; CLI compatibility depends on installed version.
        return self._parse_agent_reach_output(result["stdout"], channel)

    def _parse_agent_reach_output(
        self, output: str, platform: str
    ) -> List[TrendItemCandidate]:
        """Accept structured JSON/JSONL; never infer videos from prose output."""
        try:
            payload = json.loads(output)
        except (TypeError, ValueError):
            payload = []
            for line in str(output or "").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    payload.append(row)
        if isinstance(payload, dict):
            payload = payload.get("items", payload.get("results", payload.get("entries", [payload])))
        if not isinstance(payload, list):
            return []
        candidates = []
        for row in payload[:self._max_results]:
            if not isinstance(row, dict):
                continue
            url = str(row.get("source_url") or row.get("webpage_url") or row.get("url") or "")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
            def numeric(key):
                value = row.get(key)
                try:
                    return max(0, int(value)) if value is not None else None
                except (ValueError, TypeError, OverflowError):
                    return None
            candidates.append(TrendItemCandidate(
                platform=platform, source_url=url, title=str(row.get("title") or "")[:500],
                author=str(row.get("author") or row.get("uploader") or "")[:200],
                duration_seconds=numeric("duration"), view_count=numeric("view_count"),
                like_count=numeric("like_count"), comment_count=numeric("comment_count"),
            ))
        return candidates

    def _run_agent_reach_command(
        self, args: List[str], timeout: int
    ) -> Dict[str, Any]:
        """
        Safely run an Agent-Reach command.

        Returns:
            Dict with keys: ok, stdout, stderr, returncode, error
        """
        cmd = [self._command] + args

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,  # Never use shell=True for security
            )

            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "error": None,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "returncode": -1,
                "error": "timeout",
            }
        except FileNotFoundError:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Command '{self._command}' not found",
                "returncode": -1,
                "error": "not_found",
            }
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
                "returncode": -1,
                "error": str(exc),
            }
