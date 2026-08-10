from __future__ import annotations

import sys
import types

if "yt_dlp" not in sys.modules:
    sys.modules["yt_dlp"] = types.ModuleType("yt_dlp")

from universal_video_ai.downloader.channel import (  # noqa: E402
    ChannelListingService,
    ChannelScanResult,
    ChannelVideoCandidate,
)
from universal_video_ai.downloader.platform import Platform  # noqa: E402
from universal_video_ai.web.store import Store  # noqa: E402


def test_extract_douyin_pagination_from_nested_response() -> None:
    payload = {
        "data": {
            "aweme_list": [
                {"aweme_id": "7423763200149048613", "desc": "first"},
                {"aweme_id": "7423763200149048614", "desc": "second"},
            ],
            "has_more": 1,
            "max_cursor": 1720000000000,
        }
    }
    pages = ChannelListingService._extract_douyin_pagination(payload)
    assert pages == [{
        "has_more": True,
        "cursor": "1720000000000",
        "count": 2,
    }]


def test_extract_terminal_douyin_page() -> None:
    payload = {
        "awemeList": [{"awemeId": "7423763200149048613"}],
        "hasMore": False,
        "nextCursor": "0",
    }
    pages = ChannelListingService._extract_douyin_pagination(payload)
    assert pages[0]["has_more"] is False
    assert pages[0]["cursor"] == "0"


def test_channel_result_exposes_completion_metadata() -> None:
    result = ChannelScanResult(
        channel_url="https://www.douyin.com/user/SEC_UID",
        resolved_url="https://www.douyin.com/user/SEC_UID",
        platform=Platform.DOUYIN,
        complete=False,
        has_more=True,
        cursor="123",
        scan_source="playwright",
        stop_reason="timeout",
        network_pages=4,
    )
    data = result.to_dict(include_videos=False)
    assert data["complete"] is False
    assert data["has_more"] is True
    assert data["cursor"] == "123"
    assert data["network_pages"] == 4


def test_persistent_channel_catalog_merges_and_deduplicates(tmp_path) -> None:
    store = Store(tmp_path / "web.sqlite")
    user_id = store.create_user("deep-scan-user", "hash")
    canonical = "https://www.douyin.com/user/SEC_UID"

    first = store.merge_channel_scan_result(
        user_id,
        original_url=canonical,
        canonical_url=canonical,
        platform="douyin",
        channel_id="SEC_UID",
        channel_title="Demo",
        videos=[
            {
                "video_id": "7423763200149048613",
                "source_url": "https://www.douyin.com/video/7423763200149048613",
                "title": "A",
            },
            {
                "video_id": "7423763200149048614",
                "source_url": "https://www.douyin.com/video/7423763200149048614",
                "title": "B",
            },
        ],
        cursor="100",
        has_more=True,
        complete=False,
        stop_reason="idle_exhausted",
        scan_source="playwright",
        network_pages=1,
    )
    assert first["new_count"] == 2
    assert first["total_discovered"] == 2
    assert first["complete"] is False

    second = store.merge_channel_scan_result(
        user_id,
        original_url=canonical,
        canonical_url=canonical,
        platform="douyin",
        channel_id="SEC_UID",
        channel_title="Demo",
        videos=[
            {
                "video_id": "7423763200149048613",
                "source_url": "https://www.douyin.com/video/7423763200149048613",
                "title": "A refreshed",
            },
            {
                "video_id": "7423763200149048615",
                "source_url": "https://www.douyin.com/video/7423763200149048615",
                "title": "C",
            },
        ],
        cursor="200",
        has_more=False,
        complete=True,
        stop_reason="terminal_cursor",
        scan_source="playwright",
        network_pages=2,
    )
    assert second["new_count"] == 1
    assert second["total_discovered"] == 3
    assert second["complete"] is True
    assert store.get_channel_scan_video_ids(user_id, canonical) == {
        "7423763200149048613",
        "7423763200149048614",
        "7423763200149048615",
    }

    catalog = store.list_channel_scan_videos(user_id, canonical)
    assert [item["video_id"] for item in catalog] == [
        "7423763200149048613",
        "7423763200149048614",
        "7423763200149048615",
    ]
    assert catalog[0]["title"] == "A refreshed"

    assert store.reset_channel_scan(user_id, canonical) is True
    assert store.list_channel_scan_videos(user_id, canonical) == []
    assert store.get_channel_scan_state(user_id, canonical) is None


def test_scan_force_refresh_bypasses_memory_cache(monkeypatch) -> None:
    service = ChannelListingService(hard_limit=100)
    service.cache_ttl_seconds = 600
    calls = {"count": 0}

    monkeypatch.setattr(
        service,
        "_scan_with_ytdlp",
        lambda classification, canonical_url, effective_limit: (None, "unsupported"),
    )

    def fake_browser(classification, canonical_url, effective_limit, **kwargs):
        calls["count"] += 1
        return ChannelScanResult(
            channel_url=classification.original_url,
            resolved_url=canonical_url,
            platform=Platform.DOUYIN,
            channel_id="SEC_UID",
            complete=False,
            videos=[ChannelVideoCandidate(
                source_url="https://www.douyin.com/video/7423763200149048613",
                platform=Platform.DOUYIN,
                video_id="7423763200149048613",
            )],
        )

    monkeypatch.setattr(service, "_scan_douyin_with_playwright", fake_browser)
    url = "https://www.douyin.com/user/SEC_UID"
    service.scan(url, max_videos=3)
    service.scan(url, max_videos=3)
    assert calls["count"] == 1
    service.scan(url, max_videos=3, force_refresh=True, deep=True)
    assert calls["count"] == 2


def test_douyin_ownership_guard_filters_recommendations_from_other_creators() -> None:
    target = "MS4wLjABAAAA_TARGET_SEC_UID"
    payload = {
        "aweme_list": [
            {
                "aweme_id": "7423763200149048613",
                "desc": "video đúng kênh",
                "author": {"sec_uid": target, "nickname": "Target Creator"},
            },
            {
                "aweme_id": "7423763200149048614",
                "desc": "video đề xuất kênh khác",
                "author": {"sec_uid": "MS4wLjABAAAA_OTHER", "nickname": "Other Creator"},
            },
        ]
    }
    records: dict[str, dict] = {}
    ChannelListingService._merge_douyin_payload(
        payload,
        records,
        expected_author_id=target,
        require_owner=True,
    )
    assert list(records) == ["7423763200149048613"]
    assert records["7423763200149048613"]["owner_verified"] is True
    assert records["7423763200149048613"]["uploader"] == "Target Creator"


def test_douyin_pagination_only_accepts_target_profile_post_response() -> None:
    target = "MS4wLjABAAAA_TARGET_SEC_UID"
    assert ChannelListingService._douyin_post_response_matches_profile(
        f"https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id={target}&max_cursor=0",
        target,
    ) is True
    assert ChannelListingService._douyin_post_response_matches_profile(
        "https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=OTHER&max_cursor=0",
        target,
    ) is False
    assert ChannelListingService._douyin_post_response_matches_profile(
        "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=7423763200149048613",
        target,
    ) is False


def test_channel_catalog_scan_version_and_job_source_channel_history(tmp_path) -> None:
    store = Store(tmp_path / "web.sqlite")
    user_id = store.create_user("source-channel-user", "hash")
    canonical = "https://www.douyin.com/user/SEC_UID"
    state = store.merge_channel_scan_result(
        user_id,
        original_url=canonical,
        canonical_url=canonical,
        platform="douyin",
        channel_id="SEC_UID",
        channel_title="Kênh Test",
        videos=[{
            "video_id": "7423763200149048613",
            "source_url": "https://www.douyin.com/video/7423763200149048613",
            "uploader": "Kênh Test",
        }],
        scan_version=2,
    )
    assert int(state["scan_version"]) == 2

    job = store.create_job(user_id, "https://www.douyin.com/video/7423763200149048613", "vi")
    assert store.set_job_source_channel(
        job.id,
        user_id,
        channel_url=canonical,
        channel_title="Kênh Test",
        channel_id="SEC_UID",
        uploader="Kênh Test",
    ) is True
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.source_channel_title == "Kênh Test"
    assert loaded.source_channel_url == canonical
    searched = store.search_jobs_for_user(user_id, query="Kênh Test")
    assert [item.id for item in searched] == [job.id]
