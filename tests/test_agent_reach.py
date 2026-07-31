"""
Tests for Agent-Reach integration in Trend Scanner.
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from universal_video_ai.trends.providers.agent_reach import AgentReachProvider
from universal_video_ai.trends.models import TrendItemCandidate


class TestAgentReachProvider:
    """Test cases for AgentReachProvider."""

    def test_provider_disabled_by_default(self):
        """Test that Agent-Reach is disabled by default."""
        provider = AgentReachProvider()
        assert provider._enabled is False
        assert provider.is_available() is False

    def test_provider_enabled_with_env_var(self):
        """Test that Agent-Reach can be enabled via environment variable."""
        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            assert provider._enabled is True

    @patch("shutil.which")
    def test_is_available_command_not_found(self, mock_which):
        """Test is_available returns False when command not found."""
        mock_which.return_value = None
        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            assert provider.is_available() is False

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_is_available_doctor_fails(self, mock_run, mock_which):
        """Test is_available returns False when doctor check fails."""
        mock_which.return_value = "/usr/bin/agent-reach"
        mock_run.return_value = MagicMock(returncode=1, stderr="doctor failed")
        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            assert provider.is_available() is False

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_is_available_success(self, mock_run, mock_which):
        """Test is_available returns True when all checks pass."""
        mock_which.return_value = "/usr/bin/agent-reach"
        mock_run.return_value = MagicMock(returncode=0, stdout="doctor ok")
        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            assert provider.is_available() is True

    def test_search_returns_empty_when_not_available(self):
        """Test search returns empty list when provider not available."""
        provider = AgentReachProvider()
        results = provider.search("test topic", max_results=10)
        assert results == []

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_run_agent_reach_command_timeout(self, mock_run, mock_which):
        """Test command handling when timeout occurs."""
        from subprocess import TimeoutExpired

        mock_which.return_value = "/usr/bin/agent-reach"
        mock_run.side_effect = TimeoutExpired("agent-reach", 30)

        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            result = provider._run_agent_reach_command(["test"], timeout=30)
            assert result["ok"] is False
            assert result["error"] == "timeout"

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_run_agent_reach_command_not_found(self, mock_run, mock_which):
        """Test command handling when command not found."""
        mock_which.return_value = None
        mock_run.side_effect = FileNotFoundError()

        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            result = provider._run_agent_reach_command(["test"], timeout=30)
            assert result["ok"] is False
            assert result["error"] == "not_found"

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_run_agent_reach_command_success(self, mock_run, mock_which):
        """Test command handling when successful."""
        mock_which.return_value = "/usr/bin/agent-reach"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="test output", stderr=""
        )

        with patch.dict(os.environ, {"AGENT_REACH_ENABLED": "true"}):
            provider = AgentReachProvider()
            result = provider._run_agent_reach_command(["test"], timeout=30)
            assert result["ok"] is True
            assert result["stdout"] == "test output"

    def test_parse_agent_reach_output_placeholder(self):
        """Test that parse_agent_reach_output returns empty list (placeholder)."""
        provider = AgentReachProvider()
        results = provider._parse_agent_reach_output("mock output", "youtube")
        assert results == []

    def test_provider_name(self):
        """Test provider name property."""
        provider = AgentReachProvider()
        assert provider.provider_name == "agent_reach"


class TestTrendItemCandidate:
    """Test cases for TrendItemCandidate model."""

    def test_equality(self):
        """Test TrendItemCandidate equality based on platform and source_url."""
        item1 = TrendItemCandidate(platform="youtube", source_url="https://example.com/video1")
        item2 = TrendItemCandidate(platform="youtube", source_url="https://example.com/video1")
        item3 = TrendItemCandidate(platform="youtube", source_url="https://example.com/video2")

        assert item1 == item2
        assert item1 != item3

    def test_hash(self):
        """Test TrendItemCandidate hash for deduplication."""
        item1 = TrendItemCandidate(platform="youtube", source_url="https://example.com/video1")
        item2 = TrendItemCandidate(platform="youtube", source_url="https://example.com/video1")
        item3 = TrendItemCandidate(platform="youtube", source_url="https://example.com/video2")

        assert hash(item1) == hash(item2)
        assert hash(item1) != hash(item3)

        # Test in set
        items_set = {item1, item2, item3}
        assert len(items_set) == 2  # item1 and item2 are duplicates


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
