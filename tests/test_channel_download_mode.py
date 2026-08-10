from __future__ import annotations

from pathlib import Path

import sys
import types

if "yt_dlp" not in sys.modules:
    sys.modules["yt_dlp"] = types.ModuleType("yt_dlp")

from universal_video_ai.downloader.channel import (  # noqa: E402
    ChannelListingService,
    URLIntent,
    VideoURLClassifier,
)


def test_classifier_separates_video_and_channel_urls() -> None:
    classifier = VideoURLClassifier()
    cases = {
        "https://www.youtube.com/watch?v=abc": URLIntent.VIDEO,
        "https://youtu.be/abc": URLIntent.VIDEO,
        "https://www.youtube.com/@creator/videos": URLIntent.CHANNEL,
        "https://www.tiktok.com/@creator/video/123": URLIntent.VIDEO,
        "https://www.tiktok.com/@creator": URLIntent.CHANNEL,
        "https://www.douyin.com/video/123": URLIntent.VIDEO,
        "https://www.douyin.com/user/SEC_UID": URLIntent.CHANNEL,
        "https://www.douyin.com/user/SEC_UID?from_tab_name=main&vid=7659744912519121521": URLIntent.CHANNEL,
    }
    for url, expected in cases.items():
        assert classifier.classify(url, resolve_short=False).intent == expected


def test_douyin_profile_is_canonicalized_without_modal_query() -> None:
    classifier = VideoURLClassifier()
    classification = classifier.classify(
        "https://www.douyin.com/user/MS4wLjABAAAA9GEB?from_tab_name=main&vid=7659744912519121521",
        resolve_short=False,
    )
    assert ChannelListingService._canonical_channel_url(classification) == (
        "https://www.douyin.com/user/MS4wLjABAAAA9GEB"
    )


def test_channel_scan_normalizes_flat_youtube_entries(monkeypatch) -> None:
    fake_info = {
        "id": "channel-id",
        "title": "Demo Channel",
        "entries": [
            {"id": "aaa", "title": "A"},
            {"id": "bbb", "title": "B"},
            {"id": "aaa", "title": "duplicate"},
        ],
    }

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            assert url == "https://www.youtube.com/@creator/videos"
            return fake_info

    fake_module = types.SimpleNamespace(YoutubeDL=FakeYDL)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

    service = ChannelListingService(hard_limit=100)
    result = service.scan("https://www.youtube.com/@creator/videos")

    assert result.channel_title == "Demo Channel"
    assert [item.source_url for item in result.videos] == [
        "https://www.youtube.com/watch?v=aaa",
        "https://www.youtube.com/watch?v=bbb",
    ]


def test_douyin_unsupported_ytdlp_uses_browser_fallback(monkeypatch) -> None:
    service = ChannelListingService(hard_limit=100)

    monkeypatch.setattr(
        service,
        "_scan_with_ytdlp",
        lambda classification, canonical_url, effective_limit: (None, "Unsupported URL"),
    )

    def fake_browser(classification, canonical_url, effective_limit, **kwargs):
        assert canonical_url == "https://www.douyin.com/user/SEC_UID"
        return service._result_from_entries(
            classification=classification,
            canonical_url=canonical_url,
            entries=[{"aweme_id": "7659744912519121521", "desc": "demo"}],
            channel_title="Demo",
            channel_id="SEC_UID",
            effective_limit=effective_limit,
        )

    monkeypatch.setattr(service, "_scan_douyin_with_playwright", fake_browser)

    result = service.scan(
        "https://www.douyin.com/user/SEC_UID?from_tab_name=main&vid=7659744912519121521"
    )
    assert result.resolved_url == "https://www.douyin.com/user/SEC_UID"
    assert [item.source_url for item in result.videos] == [
        "https://www.douyin.com/video/7659744912519121521"
    ]


def test_douyin_payload_extraction_collects_network_aweme_records() -> None:
    payload = {
        "aweme_list": [
            {
                "aweme_id": "7659744912519121521",
                "desc": "Demo video",
                "create_time": 1720000000,
                "author": {"nickname": "Demo creator"},
                "video": {
                    "duration": 12345,
                    "cover": {"url_list": ["https://example.test/cover.jpg"]},
                },
            }
        ]
    }
    records = {}
    ChannelListingService._merge_douyin_payload(payload, records)
    item = records["7659744912519121521"]
    assert item["desc"] == "Demo video"
    assert item["uploader"] == "Demo creator"
    assert item["duration"] == 12.345
    assert item["thumbnail"] == "https://example.test/cover.jpg"


def test_douyin_html_extraction_reads_hydration_and_video_links() -> None:
    html = '''
    <html><body>
      <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
        {"scope":{"aweme_list":[{"aweme_id":"7659744912519121521","desc":"Hydrated"}]}}
      </script>
      <a href="https://www.douyin.com/video/7659744912519121522">video</a>
    </body></html>
    '''
    records = ChannelListingService._extract_douyin_records_from_html(html)
    assert list(records) == ["7659744912519121521", "7659744912519121522"]
    assert records["7659744912519121521"]["desc"] == "Hydrated"


def test_browser_launch_candidates_fall_back_to_system_browsers(monkeypatch) -> None:
    monkeypatch.delenv("DOUYIN_CHANNEL_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.delenv("DOUYIN_CHANNEL_BROWSER_CHANNEL", raising=False)
    candidates = ChannelListingService._playwright_launch_candidates(headless=True)
    labels = [label for label, _ in candidates]
    assert "playwright-chromium" in labels
    assert "system-chrome" in labels
    assert "system-edge" in labels
    chrome = next(options for label, options in candidates if label == "system-chrome")
    edge = next(options for label, options in candidates if label == "system-edge")
    assert chrome["channel"] == "chrome"
    assert edge["channel"] == "msedge"


def test_channel_scan_cache_avoids_scanning_same_profile_twice(monkeypatch) -> None:
    service = ChannelListingService(hard_limit=100)
    service.cache_ttl_seconds = 600
    calls = {"count": 0}

    monkeypatch.setattr(
        service,
        "_scan_with_ytdlp",
        lambda classification, canonical_url, effective_limit: (None, "Unsupported URL"),
    )

    def fake_browser(classification, canonical_url, effective_limit, **kwargs):
        calls["count"] += 1
        return service._result_from_entries(
            classification=classification,
            canonical_url=canonical_url,
            entries=[{"aweme_id": "7659744912519121521", "desc": "demo"}],
            channel_title="Demo",
            channel_id="SEC_UID",
            effective_limit=effective_limit,
        )

    monkeypatch.setattr(service, "_scan_douyin_with_playwright", fake_browser)
    first = service.scan("https://www.douyin.com/user/SEC_UID", max_videos=3)
    second = service.scan("https://www.douyin.com/user/SEC_UID", max_videos=3)
    assert calls["count"] == 1
    assert first is not second
    assert first.videos[0].source_url == second.videos[0].source_url


def test_douyin_auth_requirement_retries_visible_browser(monkeypatch) -> None:
    from universal_video_ai.downloader.channel import DouyinAuthRequired
    from universal_video_ai.downloader.platform import Platform

    service = ChannelListingService(hard_limit=100)
    monkeypatch.setenv("DOUYIN_CHANNEL_AUTO_LOGIN_RECOVERY", "true")
    monkeypatch.setattr(
        service,
        "_scan_with_ytdlp",
        lambda classification, canonical_url, effective_limit: (None, "skipped"),
    )
    calls = []

    def fake_browser(classification, canonical_url, effective_limit, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("headless_override") is not False:
            raise DouyinAuthRequired("login required")
        return service._result_from_entries(
            classification=classification,
            canonical_url=canonical_url,
            entries=[{"aweme_id": "7659744912519121521", "desc": "owned"}],
            channel_title="Demo",
            channel_id="SEC_UID",
            effective_limit=effective_limit,
        )

    monkeypatch.setattr(service, "_scan_douyin_with_playwright", fake_browser)
    result = service.scan("https://www.douyin.com/user/SEC_UID", force_refresh=True)
    assert result.platform == Platform.DOUYIN
    assert len(calls) == 2
    assert calls[1]["headless_override"] is False
    assert calls[1]["allow_auth_wait"] is True


def test_managed_douyin_profile_dir_defaults_inside_project(monkeypatch, tmp_path) -> None:
    from universal_video_ai.downloader import channel as channel_module

    monkeypatch.delenv("DOUYIN_CHANNEL_BROWSER_USER_DATA_DIR", raising=False)
    path = Path(channel_module._managed_douyin_profile_dir())
    assert path.name == "douyin_channel"
    assert path.parent.name == "browser_profiles"
    assert path.is_dir()
