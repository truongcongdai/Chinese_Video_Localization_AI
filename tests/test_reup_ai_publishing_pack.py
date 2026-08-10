from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from universal_video_ai.publishing import (
    PublishingLLMClient,
    PublishingLLMConfig,
    PublishingPackConfig,
    PublishingPackService,
)
from universal_video_ai.web.store import Store


def test_publishing_config_defaults_to_generic_and_normalizes_platforms():
    default = PublishingPackConfig()
    assert default.channel_profile == "generic_reup"
    assert default.channel_name == ""

    config = PublishingPackConfig.from_dict({
        "enabled": True,
        "channel_profile": "user-profile-1",
        "channel_name": "  Kênh   Của Tôi  ",
        "platforms": ["youtube", "youtube", "invalid", "facebook"],
        "thumbnail_count": 99,
    })
    assert config.enabled is True
    assert config.channel_name == "Kênh Của Tôi"
    assert config.platforms == ["youtube", "facebook"]
    assert config.thumbnail_count == 3


def test_store_roundtrip_and_retry_preserve_publishing_config(tmp_path):
    store = Store(tmp_path / "web.sqlite")
    user_id = store.create_user("publisher", "hash")
    publishing = {
        "enabled": True,
        "channel_profile": "generic_reup",
        "channel_name": "Kênh A",
        "platforms": ["youtube", "facebook"],
    }
    job = store.create_job(
        user_id,
        "https://example.com/video",
        "vi",
        publishing_config=publishing,
    )
    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.publishing_config == publishing
    assert loaded.publishing_pack_status == "pending"
    retry = store.retry_job(job.id, user_id)
    assert retry is not None
    assert retry.publishing_config == publishing


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")
def test_publishing_service_creates_metadata_thumbnails_and_publish_ready(tmp_path):
    source = tmp_path / "final.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=#29415f:s=360x640:d=4:r=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(source),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

    service = PublishingPackService(
        PublishingLLMClient(PublishingLLMConfig(provider="none"))
    )
    generated = service.generate(
        config=PublishingPackConfig(
            enabled=True,
            channel_profile="profile-demo",
            channel_name="Kênh Demo",
            profile_data={
                "id": "profile-demo",
                "name": "Kênh Demo",
                "channel_name": "Kênh Demo",
                "niche": "truyện tu tiên và gia tộc",
                "audience": "khán giả Việt Nam yêu thích truyện",
                "primary_keyword_groups": {"main": ["tu tiên gia tộc", "lão tổ"]},
                "base_hashtags": ["#KenhDemo"],
                "description_template": "{episode_summary}\n\n{hashtags}",
            },
        ),
        output_dir=tmp_path,
        final_video_path=source,
        source_video_path=source,
        source_url="https://www.douyin.com/video/123",
        source_metadata={
            "title": "Gia Tộc Tu Tiên Ta Trở Thành Lão Tổ",
            "platform": "douyin",
            "duration": 4,
        },
        translated_segments=[
            {"start": 0, "end": 1.2, "text": "Gia tộc họ Lâm chỉ còn ba người sống sót."},
            {"start": 1.2, "end": 2.7, "text": "Lão tổ đã bế quan suốt một vạn năm."},
            {"start": 2.7, "end": 4, "text": "Hệ thống gia tộc thức tỉnh và cả tộc bắt đầu quật khởi."},
        ],
        source_segments=[],
        user_id=1,
        job_id="job123",
        target_language="vi",
    )

    assert generated.publish_ready_video_path is not None
    assert generated.publish_ready_video_path.stat().st_size > 1024
    for index in range(1, 4):
        assert (generated.pack_dir / f"thumbnail_youtube_{index:02d}.jpg").stat().st_size > 1024
        assert (generated.pack_dir / f"thumbnail_facebook_{index:02d}.jpg").stat().st_size > 1024
    youtube = json.loads((generated.pack_dir / "youtube_metadata.json").read_text(encoding="utf-8"))
    assert youtube["privacy_status"] == "private"
    assert youtube["made_for_kids"] is False
    assert youtube["recommended_title"]
    assert "Kênh Demo" in (generated.pack_dir / "publishing_pack.md").read_text(encoding="utf-8")


def test_static_ui_contains_reup_publishing_pack_controls():
    root = Path(__file__).parents[1]
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    assert "publishing-enable-checkbox" in html
    assert "Reup tổng quát · mặc định hệ thống" in html
    assert "publishing-profile-save" in html
    assert "publishing-profile-niche" in html
    assert "getPublishingPackConfig" in js
    assert "AI Publishing Pack" in js


def test_van_diep_profile_flags_unrelated_content_instead_of_forcing_tu_tien(tmp_path):
    from universal_video_ai.publishing.profiles import get_channel_profile
    from universal_video_ai.publishing.service import _deterministic_pack

    profile = get_channel_profile("van_diep_studio", "Vạn Diệp Studio")
    config = PublishingPackConfig(enabled=True)
    payload = _deterministic_pack(
        profile=profile,
        config=config,
        source_metadata={"title": "Top 3 giống thỏ đắt nhất thế giới"},
        translated_segments=[],
        translated_text=(
            "Giống thỏ này có đôi mắt rất hiền và bộ lông mềm. "
            "Một con thỏ thuần chủng có thể có giá hơn hai nghìn đô la."
        ),
        source_text="",
    )

    analysis = payload["content_analysis"]
    assert analysis["channel_fit"] == "review_before_publish"
    assert analysis["primary_keyword"] in (None, "")
    assert "tu tiên" not in payload["youtube"]["recommended_title"].lower()
    assert analysis["story_name"] == "Chưa xác định"


def test_download_cache_preserves_source_metadata(tmp_path):
    import importlib.util
    import sys

    module_path = Path(__file__).parents[1] / "src" / "universal_video_ai" / "downloader" / "download_cache.py"
    spec = importlib.util.spec_from_file_location("_uvai_download_cache_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    DownloadCache = module.DownloadCache

    video = tmp_path / "source.mp4"
    video.write_bytes(b"not-a-real-video-but-enough-for-cache")
    cache = DownloadCache(tmp_path / "cache")
    cache.put(
        "https://example.com/video/1",
        video,
        source_metadata={
            "platform": "douyin",
            "title": "Tên video gốc",
            "description": "Mô tả gốc",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "tags": ["tag1"],
        },
    )
    entry = cache.get_entry("https://example.com/video/1")
    assert entry is not None
    assert entry["source_metadata"]["title"] == "Tên video gốc"
    assert entry["source_metadata"]["description"] == "Mô tả gốc"


def test_component_status_and_retry_only_failed_assets(tmp_path, monkeypatch):
    import universal_video_ai.publishing.service as publishing_service

    final_video = tmp_path / "final.mp4"
    final_video.write_bytes(b"finished-video-must-not-be-touched")
    original_bytes = final_video.read_bytes()

    monkeypatch.setattr(publishing_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(publishing_service, "_probe_duration", lambda path: 12.0)

    def fail_thumbnail(*args, **kwargs):
        raise RuntimeError("simulated thumbnail failure")

    monkeypatch.setattr(publishing_service, "_render_thumbnail", fail_thumbnail)
    service = PublishingPackService(PublishingLLMClient(PublishingLLMConfig(provider="none")))
    config = PublishingPackConfig(
        enabled=True,
        generate_thumbnails=True,
        generate_publish_ready_video=False,
    )
    common = dict(
        config=config,
        output_dir=tmp_path,
        final_video_path=final_video,
        source_video_path=None,
        source_url="https://www.douyin.com/video/123",
        source_metadata={"title": "Gia Tộc Tu Tiên", "platform": "douyin"},
        translated_segments=[
            {"start": 0.0, "end": 2.0, "text": "Gia tộc chỉ còn ba người."},
            {"start": 2.0, "end": 4.0, "text": "Lão tổ trở về và gia tộc quật khởi."},
        ],
        source_segments=[],
        user_id=1,
        job_id="job-retry",
        target_language="vi",
    )
    first = service.generate(**common)
    assert first.overall_status == "partial"
    state = json.loads((first.pack_dir / "component_status.json").read_text(encoding="utf-8"))
    assert state["components"]["analysis"]["status"] == "success"
    assert state["components"]["youtube_metadata"]["status"] == "success"
    assert state["components"]["youtube_thumbnails"]["status"] == "failed"
    assert state["components"]["facebook_thumbnails"]["status"] == "failed"
    assert state["components"]["publish_ready"]["status"] == "skipped"
    assert final_video.read_bytes() == original_bytes

    def success_thumbnail(video_path, output_path, **kwargs):
        Path(output_path).write_bytes(b"jpeg" * 400)

    monkeypatch.setattr(publishing_service, "_render_thumbnail", success_thumbnail)
    retried = service.retry_components(component="failed", **common)
    assert retried.overall_status == "success"
    state = json.loads((retried.pack_dir / "component_status.json").read_text(encoding="utf-8"))
    assert state["components"]["youtube_thumbnails"]["status"] == "success"
    assert state["components"]["facebook_thumbnails"]["status"] == "success"
    assert state["components"]["youtube_thumbnails"]["attempts"] == 2
    assert state["components"]["analysis"]["attempts"] == 1
    assert (retried.pack_dir / "thumbnail_youtube_01.jpg").is_file()
    assert (retried.pack_dir / "thumbnail_facebook_01.jpg").is_file()
    assert final_video.read_bytes() == original_bytes


def test_static_ui_contains_component_retry_controls():
    root = Path(__file__).parents[1]
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    assert "Thử lại tất cả phần lỗi" in js
    assert "publishing-pack/retry" in js
    assert "youtube_thumbnails" in js
    assert "Tạo lại thủ công" in js


def test_backend_exposes_selective_publishing_retry_route():
    root = Path(__file__).parents[1]
    app_source = (root / "src/universal_video_ai/web/app.py").read_text(encoding="utf-8")
    assert '/api/jobs/{job_id}/publishing-pack/retry' in app_source
    assert "PublishingPackRetryBody" in app_source
    assert "retry_components(" in app_source


def test_saved_publishing_profiles_are_isolated_per_user(tmp_path):
    store = Store(tmp_path / "web.sqlite")
    user_a = store.create_user("a", "hash")
    user_b = store.create_user("b", "hash")
    saved = store.save_publishing_profile(
        user_a,
        name="Kênh A",
        profile={
            "channel_name": "Kênh A",
            "niche": "động vật",
            "primary_keyword_groups": {"main": ["động vật", "thỏ"]},
        },
        is_default=True,
    )
    assert len(store.list_publishing_profiles(user_a)) == 1
    assert store.list_publishing_profiles(user_b) == []
    assert store.get_publishing_profile(user_b, saved["id"]) is None


def test_custom_profile_niche_matching_is_not_van_diep_hardcoded():
    from universal_video_ai.publishing.profiles import normalize_channel_profile
    from universal_video_ai.publishing.service import _deterministic_pack

    profile = normalize_channel_profile({
        "id": "pets",
        "name": "Kênh Thú Cưng",
        "channel_name": "Kênh Thú Cưng",
        "niche": "động vật, thú cưng và kiến thức về thỏ",
        "audience": "người yêu động vật",
        "primary_keyword_groups": {"main": ["giống thỏ", "thú cưng", "động vật"]},
        "base_hashtags": ["#ThuCung"],
        "description_template": "{episode_summary}\n\n{hashtags}",
    }, profile_id="pets")
    payload = _deterministic_pack(
        profile=profile,
        config=PublishingPackConfig(enabled=True, channel_profile="pets", channel_name="Kênh Thú Cưng"),
        source_metadata={"title": "Top 3 giống thỏ đắt nhất thế giới"},
        translated_segments=[],
        translated_text="Ba giống thỏ quý hiếm có bộ lông đẹp và giá rất cao. Giống thỏ cuối cùng có giá hơn hai nghìn đô la.",
        source_text="",
    )
    assert payload["content_analysis"]["channel_fit"] == "matched"
    assert "tu tiên" not in payload["youtube"]["recommended_title"].lower()


def test_static_ui_no_longer_auto_selects_van_diep_globally():
    root = Path(__file__).parents[1]
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    assert 'value="van_diep_studio" selected' not in html
    assert 'value="generic_reup" selected' in html
    assert 'loadPublishingProfiles();' in js
    assert '/api/publishing/profiles' in js


def test_publishing_config_accepts_hook_drama_viral_styles():
    assert PublishingPackConfig.from_dict({"style": "hook"}).style == "hook"
    assert PublishingPackConfig.from_dict({"style": "drama"}).style == "drama"
    assert PublishingPackConfig.from_dict({"style": "viral"}).style == "viral"
    assert PublishingPackConfig.from_dict({"style": "search"}).style == "seo"
    assert PublishingPackConfig.from_dict({"style": "curiosity"}).style == "hook"


def test_deterministic_pack_generates_clickable_titles_and_thumbnail_texts():
    from universal_video_ai.publishing.service import _deterministic_pack

    profile = {
        "id": "profile-demo",
        "name": "Kênh Demo",
        "channel_name": "Kênh Demo",
        "niche": "review truyện và drama cổ trang",
        "audience": "khán giả thích chuyện drama",
        "category": "Entertainment",
        "made_for_kids": False,
        "default_privacy": "private",
        "primary_keyword_groups": {"main": ["review truyện"]},
        "base_tags": [],
        "base_hashtags": ["#KenhDemo"],
        "thumbnail_rules": {"max_words": 5, "examples": []},
        "description_template": "{episode_summary}\n\n{hashtags}",
    }
    payload = _deterministic_pack(
        profile=profile,
        config=PublishingPackConfig(enabled=True, style="drama"),
        source_metadata={"title": "Đòi lại của hồi môn ở phủ Tướng gia"},
        translated_segments=[],
        translated_text=(
            "Thẩm Nam Kiều quyết liệt đòi lại của hồi môn ngay trước phủ Tướng gia. "
            "Mẹ kế và cha ruột tìm cách ngăn cản nhưng cô không nhịn nữa. "
            "Cuối cùng sự thật bị vạch trần trước đám đông."
        ),
        source_text="",
    )
    assert payload["youtube"]["recommended_title"]
    assert any(
        word in payload["youtube"]["recommended_title"].lower()
        for word in ["bùng nổ", "gây sốc", "đòi", "vạch trần"]
    )
    assert len(payload["thumbnail_texts"]) == 3
    assert all(1 <= len(item.split()) <= 7 for item in payload["thumbnail_texts"])


def test_ui_has_auto_aspect_and_channel_provenance_history():
    root = Path(__file__).parents[1]
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    assert 'value="auto" selected' in html
    assert "Auto theo từng video" in html
    assert "source_channel_title" in js
    assert "Kênh nguồn:" in js
    assert "Video gốc:" in js
    assert 'aspect === "auto"' in js
