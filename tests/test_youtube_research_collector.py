from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
import time
from types import SimpleNamespace

import pytest

from universal_video_ai.analytics.youtube_research.collector import (
    YtDlpYouTubeResearchCollector,
    YouTubeCollectorError,
    YouTubeCollectorTimeoutError,
)


def test_collector_normalizes_partial_metadata_deduplicates_and_bounds() -> None:
    observed = datetime(2026, 9, 3, tzinfo=timezone.utc)
    result = {
        "entries": [
            {
                "id": "abc123def45",
                "title": "First",
                "channel_id": "channel-1",
                "channel": "Channel One",
                "upload_date": "20260901",
                "view_count": 100,
                "like_count": None,
                "comment_count": None,
                "channel_follower_count": None,
                "duration": 61,
                "thumbnail": "https://img.example/one.jpg",
            },
            {"id": "abc123def45", "title": "Duplicate"},
            {"id": "xyz987uvw65", "title": "Second"},
            {"id": "beyondbound1", "title": "Third"},
        ]
    }
    videos = YtDlpYouTubeResearchCollector.normalize_search_result(
        result, "test query", 2, collected_at=observed
    )

    assert [video.video_id for video in videos] == ["abc123def45", "xyz987uvw65"]
    assert videos[0].canonical_url == "https://www.youtube.com/watch?v=abc123def45"
    assert videos[0].published_at == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert videos[0].view_count == 100
    assert videos[0].like_count is None
    assert videos[0].comment_count is None
    assert videos[0].subscriber_count is None
    assert videos[1].published_at is None
    assert videos[1].view_count is None
    assert videos[0].collected_at == observed


def test_collector_rejects_malformed_entries() -> None:
    with pytest.raises(YouTubeCollectorError, match="malformed entries"):
        YtDlpYouTubeResearchCollector.normalize_search_result(
            {"entries": "not-a-list"}, "query", 5
        )
    with pytest.raises(YouTubeCollectorError, match="no usable"):
        YtDlpYouTubeResearchCollector.normalize_search_result(
            {"entries": [None, {"title": "missing id"}]}, "query", 5
        )


def test_collector_enforces_hard_max_before_extraction(monkeypatch) -> None:
    collector = YtDlpYouTubeResearchCollector(
        hard_max_results=3, timeout_seconds=5
    )
    monkeypatch.setattr(collector, "is_available", lambda: True)
    monkeypatch.setattr(
        collector, "_extract_sync",
        lambda _query, _limit: pytest.fail("extractor must not run"),
    )
    with pytest.raises(ValueError, match="between 1 and 3"):
        asyncio.run(collector.search("query", 4))


def test_collector_timeout_is_explicit(monkeypatch) -> None:
    collector = YtDlpYouTubeResearchCollector(
        hard_max_results=3, timeout_seconds=1
    )
    collector.timeout_seconds = 0.01
    monkeypatch.setattr(collector, "is_available", lambda: True)

    def slow_extract(_query, _limit):
        time.sleep(0.05)
        return {"entries": []}

    monkeypatch.setattr(collector, "_extract_sync", slow_extract)
    with pytest.raises(YouTubeCollectorTimeoutError):
        asyncio.run(collector.search("query", 2))


def test_collector_wraps_extractor_errors_without_fake_results(monkeypatch) -> None:
    collector = YtDlpYouTubeResearchCollector(
        hard_max_results=3, timeout_seconds=2
    )
    monkeypatch.setattr(collector, "is_available", lambda: True)

    def fail(_query, _limit):
        raise RuntimeError("provider secret detail")

    monkeypatch.setattr(collector, "_extract_sync", fail)
    with pytest.raises(YouTubeCollectorError, match="metadata collection failed") as exc:
        asyncio.run(collector.search("query", 2))
    assert "provider secret detail" not in str(exc.value)


def test_ytdlp_call_is_metadata_only_and_bounded(monkeypatch) -> None:
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, query, download):
            captured["query"] = query
            captured["download"] = download
            return {"entries": []}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    collector = YtDlpYouTubeResearchCollector(
        hard_max_results=5, timeout_seconds=7
    )
    result = collector._extract_sync("safe query", 3)

    assert result == {"entries": []}
    assert captured["query"] == "ytsearch3:safe query"
    assert captured["download"] is False
    assert captured["options"]["skip_download"] is True
    assert captured["options"]["playlistend"] == 3
    assert captured["options"]["socket_timeout"] == 7
