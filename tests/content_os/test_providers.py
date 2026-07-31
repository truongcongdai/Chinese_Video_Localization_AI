"""
Tests for Content OS trend providers.

Tests base provider interface, downloader adapter, manual provider,
and scoring utilities.
"""
import pytest
from pathlib import Path
from datetime import datetime, timedelta

from universal_video_ai.content_os.providers.base import TrendProvider, TrendSource
from universal_video_ai.content_os.providers.downloader_adapter import DownloaderAdapter
from universal_video_ai.content_os.providers.manual_provider import ManualProvider
from universal_video_ai.content_os.providers.scoring import score_sources, filter_by_risk


class TestTrendSource:
    """Test TrendSource dataclass."""
    
    def test_creation(self):
        source = TrendSource(
            platform="youtube",
            source_url="https://youtube.com/watch?v=test",
            canonical_url="https://youtube.com/watch?v=test",
            title="Test Video",
        )
        
        assert source.platform == "youtube"
        assert source.title == "Test Video"
        assert source.metrics == {}
        assert source.raw_metadata == {}
    
    def test_with_metrics(self):
        source = TrendSource(
            platform="youtube",
            source_url="https://youtube.com/watch?v=test",
            canonical_url="https://youtube.com/watch?v=test",
            title="Test Video",
            metrics={"view_count": 1000, "like_count": 100},
        )
        
        assert source.metrics["view_count"] == 1000
        assert source.metrics["like_count"] == 100


class TestDownloaderAdapter:
    """Test DownloaderAdapter."""
    
    @pytest.fixture
    def adapter(self):
        return DownloaderAdapter(user_id=1)
    
    def test_provider_name(self, adapter):
        assert adapter.provider_name == "downloader_adapter"
    
    def test_supported_platforms(self, adapter):
        platforms = adapter.supported_platforms
        assert "youtube" in platforms
        assert "tiktok" in platforms
        assert "douyin" in platforms
    
    def test_is_available(self, adapter):
        assert adapter.is_available() is True
    
    def test_search_trends_empty(self, adapter):
        """Downloader adapter doesn't discover trends."""
        results = adapter.search_trends("cooking", "youtube", limit=10)
        assert results == []
    
    def test_validate_valid_url(self, adapter):
        result = adapter.validate_url("https://youtube.com/watch?v=test")
        assert result["valid"] is True
        assert "platform" in result
    
    def test_validate_invalid_url(self, adapter):
        result = adapter.validate_url("not-a-url")
        assert result["valid"] is False
        assert "error" in result


class TestManualProvider:
    """Test ManualProvider."""
    
    @pytest.fixture
    def provider(self):
        return ManualProvider()
    
    def test_provider_name(self, provider):
        assert provider.provider_name == "manual"
    
    def test_supported_platforms(self, provider):
        platforms = provider.supported_platforms
        assert "youtube" in platforms
        assert "tiktok" in platforms
        assert "facebook" in platforms
    
    def test_is_available(self, provider):
        assert provider.is_available() is True
    
    def test_search_trends_empty(self, provider):
        """Manual provider doesn't search."""
        results = provider.search_trends("cooking", "youtube", limit=10)
        assert results == []
    
    def test_validate_valid_urls(self, provider):
        urls = [
            "https://youtube.com/watch?v=test",
            "https://tiktok.com/@user/video/test",
        ]
        results = provider.validate_urls(urls)
        
        assert len(results) == 2
        assert all(r["valid"] for r in results)
    
    def test_validate_invalid_url(self, provider):
        result = provider.validate_url("not-a-url")
        assert result["valid"] is False
    
    def test_detect_platform_youtube(self, provider):
        result = provider.validate_url("https://youtube.com/watch?v=test")
        assert result["platform"] == "youtube"
    
    def test_detect_platform_tiktok(self, provider):
        result = provider.validate_url("https://tiktok.com/@user/video/test")
        assert result["platform"] == "tiktok"
    
    def test_create_source_from_url(self, provider):
        source = provider.create_source_from_url(
            "https://youtube.com/watch?v=test",
            "Test Video",
            {"view_count": 1000},
        )
        
        assert source.platform == "youtube"
        assert source.title == "Test Video"
        assert source.metrics["view_count"] == 1000


class TestScoring:
    """Test trend scoring utilities."""
    
    def test_score_sources_basic(self):
        sources = [
            TrendSource(
                platform="youtube",
                source_url="https://youtube.com/1",
                canonical_url="https://youtube.com/1",
                title="Video 1",
                metrics={"view_count": 1000, "like_count": 100},
            ),
            TrendSource(
                platform="youtube",
                source_url="https://youtube.com/2",
                canonical_url="https://youtube.com/2",
                title="Video 2",
                metrics={"view_count": 10000, "like_count": 1000},
            ),
        ]
        
        scored = score_sources(sources)
        
        assert len(scored) == 2
        assert scored[0].metrics["view_count"] == 10000  # Higher views first
        assert "trend_score" in scored[0].metrics
    
    def test_score_sources_with_relevance(self):
        sources = [
            TrendSource(
                platform="youtube",
                source_url="https://youtube.com/1",
                canonical_url="https://youtube.com/1",
                title="Video 1",
                metrics={"view_count": 1000},
            ),
        ]
        
        query_relevance = {"https://youtube.com/1": 0.9}
        scored = score_sources(sources, query_relevance=query_relevance)
        
        assert scored[0].metrics["relevance_score"] == 0.9
    
    def test_filter_by_risk(self):
        sources = [
            TrendSource(
                platform="youtube",
                source_url="https://youtube.com/1",
                canonical_url="https://youtube.com/1",
                title="Video 1",
                metrics={"reuse_risk": "low", "copyright_risk": "low"},
            ),
            TrendSource(
                platform="youtube",
                source_url="https://youtube.com/2",
                canonical_url="https://youtube.com/2",
                title="Video 2",
                metrics={"reuse_risk": "high", "copyright_risk": "low"},
            ),
        ]
        
        filtered = filter_by_risk(sources, max_reuse_risk="medium")
        
        assert len(filtered) == 1
        assert filtered[0].title == "Video 1"
    
    def test_filter_allows_medium(self):
        sources = [
            TrendSource(
                platform="youtube",
                source_url="https://youtube.com/1",
                canonical_url="https://youtube.com/1",
                title="Video 1",
                metrics={"reuse_risk": "medium", "copyright_risk": "low"},
            ),
        ]
        
        filtered = filter_by_risk(sources, max_reuse_risk="medium")
        
        assert len(filtered) == 1
