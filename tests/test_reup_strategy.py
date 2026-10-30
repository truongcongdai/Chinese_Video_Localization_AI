"""Channel-first discovery uses observed metadata and durable page checkpoints."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from universal_video_ai.channel_agent.youtube import YouTubeChannelIdentity
from universal_video_ai.reup.discovery import Candidate, SourceDiscovery, source_identity
from universal_video_ai.reup.strategy import ReupStrategyService
from universal_video_ai.web.store import Store


def video(identity: str, *, channel: str = "mine", views: int = 100, category: str = "27") -> dict:
    return {"id": identity, "snippet": {"channelId": channel, "title": "Khoa học thú vị", "tags": ["khoa học", "science"],
            "categoryId": category, "publishedAt": "2026-10-20T00:00:00Z"}, "statistics": {"viewCount": str(views)}}


class YouTube:
    def __init__(self, pages: int = 1):
        self.pages = pages
        self.channel = YouTubeChannelIdentity("mine", "My channel", video_count=pages, uploads_playlist_id="uploads")
        self.calls = []
        self.fail_page = None
        self.market_unavailable = False

    def get_own_channel(self, user_id):
        return self.channel

    def data_request(self, user_id, resource, params):
        self.calls.append((resource, dict(params)))
        if resource == "playlistItems":
            page = int(params.get("pageToken", "0"))
            if page == self.fail_page:
                raise ValueError("temporary provider failure")
            return {"items": [{"contentDetails": {"videoId": f"own{page:08d}"}}] if self.pages else [],
                    **({"nextPageToken": str(page + 1)} if page + 1 < self.pages else {})}
        if resource == "videoCategories":
            return {"items": [{"id": "27", "snippet": {"title": "Giáo dục"}}]}
        if params.get("chart") == "mostPopular":
            return {"items": [] if self.market_unavailable else [video("market00001", channel="other", views=10000)]}
        return {"items": [video(identity) for identity in params["id"].split(",")]}


class Discovery:
    def market(self, platform):
        raise ValueError("public feed unavailable")

    def search(self, platform, query, limit=5):
        return []


class SocialDiscovery(Discovery):
    def __init__(self, samples, related=()):
        self.samples, self.related, self.queries = samples, related, []

    def market(self, platform):
        return self.samples

    def search(self, platform, query, limit=5):
        self.queries.append((platform, query))
        return self.related


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "strategy.sqlite")
    owner = store.create_user("owner", "hash")
    other = store.create_user("other", "hash")
    return store, owner, other


def profile(**values):
    return {"strategy_mode": "auto", "market_region": "VN", "platforms": ["youtube"], **values}


def test_all_upload_pages_resume_across_batches_and_process_restart(setup):
    store, owner, _ = setup
    youtube = YouTube(pages=12)
    service = ReupStrategyService(store, youtube, Discovery())
    pending = service.analyze(owner, profile())
    assert pending["status"] == "scanning" and pending["videos_scanned"] == 10
    assert pending["report"] is None
    restarted = ReupStrategyService(store, youtube, Discovery())
    ready = restarted.analyze(owner, profile())
    assert ready["status"] == "ready" and ready["report"]["videos_analyzed"] == 12
    assert ready["report"]["catalog_complete"] is True
    assert len(restarted.own_video_ids(owner, "mine")) == 12
    assert [int(params.get("pageToken", "0")) for resource, params in youtube.calls if resource == "playlistItems"] == list(range(12))


def test_new_channel_uses_real_market_data_and_labels_public_sample_failures(setup):
    store, owner, _ = setup
    youtube = YouTube(pages=0)
    service = ReupStrategyService(store, youtube, Discovery())
    result = service.analyze(owner, profile(platforms=["youtube", "douyin", "tiktok"]))["report"]
    assert result["mode"] == "new_channel" and result["videos_analyzed"] == 0
    assert result["market_sample_size"] == 1 and result["directions"]
    assert any("douyin" in warning for warning in result["warnings"])
    assert any("tiktok" in warning for warning in result["warnings"])
    assert result["directions"][0]["search_queries"]["douyin"] == ["科普"]  # Known concepts work locally without sending untranslated terms.
    assert result["market_coverage"] == [{"platform": "youtube", "basis": "mostPopular", "sample_size": 1, "region": "VN"}]
    assert set(result["directions"][0]["search_queries"]) == {"youtube", "douyin", "tiktok"}
    assert any(resource == "videos" and params.get("chart") == "mostPopular" and params["regionCode"] == "VN" for resource, params in youtube.calls)


def test_provider_failure_does_not_become_a_new_channel_and_checkpoint_survives(setup):
    store, owner, _ = setup
    youtube = YouTube(pages=3)
    youtube.fail_page = 1
    service = ReupStrategyService(store, youtube, Discovery())
    with pytest.raises(ValueError, match="temporary"):
        service.analyze(owner, profile())
    assert service.status(owner)["status"] == "error"
    assert service.status(owner)["report"] is None
    assert service.status(owner)["videos_scanned"] == 1
    youtube.fail_page = None
    result = ReupStrategyService(store, youtube, Discovery()).analyze(owner, profile())
    assert result["report"]["mode"] == "existing_channel"
    pages = [int(params.get("pageToken", "0")) for resource, params in youtube.calls if resource == "playlistItems"]
    assert pages == [0, 1, 1, 2]


def test_scope_and_cache_do_not_mix_users_or_destination_channels(setup):
    store, owner, other = setup
    youtube = YouTube()
    service = ReupStrategyService(store, youtube, Discovery())
    service.analyze(owner, profile())
    assert service.status(other)["report"] is None
    assert service.own_video_ids(other, "mine") == set()
    pages_before = sum(resource == "playlistItems" for resource, _ in youtube.calls)
    service.analyze(owner, profile())
    assert sum(resource == "playlistItems" for resource, _ in youtube.calls) == pages_before
    with pytest.raises(ValueError, match="không khớp"):
        service.analyze(owner, profile(channel_id="foreign"))
    youtube.channel = replace(youtube.channel, channel_id="new-channel", video_count=0)
    youtube.pages = 0
    report = service.analyze(owner, profile())["report"]
    assert report["channel"]["channel_id"] == "new-channel" and report["mode"] == "new_channel"


def test_empty_or_unreadable_market_never_creates_fake_trend_suggestions(setup):
    store, owner, _ = setup
    youtube = YouTube(pages=0)
    youtube.market_unavailable = True
    service = ReupStrategyService(store, youtube, Discovery())
    with pytest.raises(ValueError, match="dữ liệu thị trường"):
        service.analyze(owner, profile())
    assert service.status(owner)["report"] is None
    assert service.status(owner)["status"] == "error"


def test_declared_existing_channel_with_no_metadata_is_not_treated_as_new(setup):
    store, owner, _ = setup
    youtube = YouTube(pages=0)
    youtube.channel = replace(youtube.channel, video_count=20)
    service = ReupStrategyService(store, youtube, Discovery())
    with pytest.raises(ValueError, match="Kênh có video"):
        service.analyze(owner, profile())
    assert service.status(owner)["report"] is None


def test_tiktok_source_canonicalization_and_platform_specific_queries(monkeypatch):
    assert source_identity("https://www.tiktok.com/@creator/video/123456789?lang=vi") == ("tiktok", "123456789", "https://www.tiktok.com/@creator/video/123456789")
    with pytest.raises(ValueError):
        source_identity("https://www.tiktok.com.evil.test/@creator/video/123456789")
    discovery = SourceDiscovery()
    calls = []
    def search(platform, query):
        calls.append((platform, query))
        return [Candidate("https://www.tiktok.com/@creator/video/123456789", "tiktok", "123456789", "Science fun")]
    monkeypatch.setattr(discovery, "search", search)
    result = discovery.scan({"platforms": ["tiktok"], "search_queries": {"tiktok": ["science fun"]}, "min_seconds": 0, "max_seconds": 60})
    assert calls == [("tiktok", "science fun")]
    assert len(result["candidates"]) == 1


def test_automatic_strategy_drives_source_selection_localization_and_publish_once(setup, tmp_path, monkeypatch):
    import asyncio
    from universal_video_ai.web.reup_router import ReupProfileBody, ReupRuntime
    store, owner, _ = setup
    submitted, searched, published = [], [], []
    async def submit(payload, user_id, job_id):
        submitted.append(payload)
        job = store.create_job(user_id, payload["url"], "vi", job_id=job_id)
        return job.to_dict()
    runtime = ReupRuntime(store, submit, tmp_path)
    runtime.strategy.youtube = YouTube()
    config = ReupProfileBody(strategy_mode="auto", platforms=["youtube"], automation="auto",
                             rights_confirmed=True).model_dump()
    runtime.repository.save_profile(owner, config)
    def discover(effective):
        searched.append(effective)
        return {"candidates": [
            {"url": "https://youtu.be/own00000000", "platform": "youtube", "video_id": "own00000000"},
            {"url": "https://youtu.be/abcdefghijk", "platform": "youtube", "video_id": "abcdefghijk", "title": "Science source", "duration": 45}], "warnings": []}
    monkeypatch.setattr(runtime.discovery, "scan", discover)
    monkeypatch.setattr(runtime, "_package", lambda *args: {"video_path": "ready.mp4"})
    def publish(user_id, run):
        published.append(run["id"])
        runtime.repository.update(user_id, run["id"], status="published")
    monkeypatch.setattr(runtime, "_publish", publish)

    async def scenario():
        first, second = await asyncio.gather(runtime.scan(owner), runtime.scan(owner))
        assert first == second and len(searched) == 1
        assert len(first["runs"]) == 1  # Own-channel uploads are never reup sources.
        run = first["runs"][0]
        assert run["status"] == "queued" and run["channel_id"] == "mine"
        assert searched[0]["search_queries"]["youtube"]
        assert run["config"]["strategy_evidence"]["mode"] == "existing_channel"
        localized = await runtime.advance(owner, run["id"])
        assert localized["status"] == "localizing" and submitted[0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
        store.update_job(localized["job_id"], status="done")
        assert (await runtime.advance(owner, run["id"]))["status"] == "ready"
        assert (await runtime.advance(owner, run["id"]))["status"] == "published"
        await runtime.advance(owner, run["id"])
        assert published == [run["id"]]
    asyncio.run(scenario())


def test_profile_change_during_source_discovery_does_not_enqueue_stale_automatic_work(setup, tmp_path, monkeypatch):
    import asyncio
    from universal_video_ai.web.reup_router import ReupProfileBody, ReupRuntime
    store, owner, _ = setup
    async def submit(*args):
        raise AssertionError("No job should be submitted")
    runtime = ReupRuntime(store, submit, tmp_path)
    config = ReupProfileBody(topic="science", platforms=["youtube"]).model_dump()
    runtime.repository.save_profile(owner, config)
    def discover(effective):
        runtime.repository.save_profile(owner, {**config, "topic": "different topic"})
        return {"candidates": [{"url": "https://youtu.be/abcdefghijk"}], "warnings": []}
    monkeypatch.setattr(runtime.discovery, "scan", discover)
    with pytest.raises(ValueError, match="Cấu hình vừa thay đổi"):
        asyncio.run(runtime.scan(owner))
    assert runtime.repository.list(owner) == []


def test_new_channel_builds_strategy_from_social_even_when_youtube_market_is_unavailable(setup):
    store, owner, _ = setup
    youtube = YouTube(pages=0)
    youtube.market_unavailable = True
    discovery = SocialDiscovery([Candidate("https://www.tiktok.com/@creator/video/123456789", "tiktok", "123456789",
        "Science experiments #science", view_count=20000, like_count=1400, tags=("science",),
        published_at=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat())])
    service = ReupStrategyService(store, youtube, discovery)
    report = service.analyze(owner, profile(platforms=["tiktok"]))["report"]
    assert report["mode"] == "new_channel" and report["videos_analyzed"] == 0
    assert report["market_sample_size"] == 1
    direction = report["directions"][0]
    assert direction["topic"].startswith("science")
    assert direction["evidence"][0]["platform"] == "tiktok"
    assert direction["social_evidence"][0]["url"].startswith("https://www.tiktok.com/")
    assert direction["plan"]["goal"] == "Thử nghiệm hướng nội dung cho kênh mới"
    assert direction["plan"]["evidence_platforms"] == ["tiktok"]
    assert any("YouTube" in warning for warning in report["warnings"])


def test_existing_channel_guides_social_queries_and_never_switches_to_an_unrelated_hot_topic(setup):
    store, owner, _ = setup
    science = Candidate("https://www.tiktok.com/@creator/video/123456789", "tiktok", "123456789",
                        "science fun #science", view_count=12000, tags=("science",))
    cooking = Candidate("https://www.tiktok.com/@creator/video/987654321", "tiktok", "987654321",
                        "cooking recipe #cooking", view_count=1000000, tags=("cooking",))
    discovery = SocialDiscovery([cooking], related=[science, cooking])
    service = ReupStrategyService(store, YouTube(), discovery)
    report = service.analyze(owner, profile(platforms=["tiktok"], topic="cooking", keywords=["cooking"]))["report"]
    assert report["mode"] == "existing_channel"
    direction = report["directions"][0]
    assert "cooking" not in direction["topic"]
    assert direction["evidence"][0]["video_id"] == "own00000000"
    assert discovery.queries and any(term in discovery.queries[0][1] for term in ("khoa học", "science"))
    # The first platform query is native channel metadata; unrelated results are rejected.
    assert any(item["video_id"] == science.video_id for item in direction["social_evidence"])
    assert not any(item["video_id"] == cooking.video_id for item in direction["social_evidence"])
    assert direction["plan"]["goal"] == "Phát triển tiếp nội dung đã có khán giả"


def test_new_channel_prefers_recent_interest_over_old_lifetime_views(setup):
    store, owner, _ = setup
    now = datetime.now(timezone.utc)
    samples = [
        Candidate("https://www.tiktok.com/@creator/video/111111111", "tiktok", "111111111", "old #old",
                  view_count=1000000, published_at=(now - timedelta(days=1000)).isoformat(), tags=("old",)),
        Candidate("https://www.tiktok.com/@creator/video/222222222", "tiktok", "222222222", "fresh #fresh",
                  view_count=20000, published_at=(now - timedelta(hours=2)).isoformat(), tags=("fresh",))]
    youtube = YouTube(pages=0)
    youtube.market_unavailable = True
    report = ReupStrategyService(store, youtube, SocialDiscovery(samples)).analyze(owner, profile(platforms=["tiktok"]))["report"]
    assert report["directions"][0]["topic"] == "fresh"
    assert report["directions"][0]["social_evidence"][0]["ranking"]["observed_growth"] is None


def test_cached_social_candidates_can_run_without_manual_sources_or_working_search(setup, tmp_path, monkeypatch):
    import asyncio
    from universal_video_ai.web.reup_router import ReupProfileBody, ReupRuntime
    store, owner, _ = setup
    async def submit(*args):
        pytest.fail("Scanning queues work; it must not submit immediately")
    runtime = ReupRuntime(store, submit, tmp_path)
    runtime.strategy.youtube = YouTube()
    runtime.repository.save_profile(owner, ReupProfileBody(strategy_mode="auto", platforms=["tiktok"],
        topic="stale manual topic", keywords=["stale filter"], source_urls=["https://youtu.be/abcdefghijk"]).model_dump())
    monkeypatch.setattr(runtime.discovery, "market", lambda *args: [])
    searches = []
    def search(platform, query, limit=10):
        searches.append(query)
        if len(searches) > 1:
            raise ValueError("Temporarily unavailable")
        return [Candidate("https://www.tiktok.com/@creator/video/123456789", "tiktok", "123456789",
                          "khoa học thú vị", duration=45, view_count=10000, like_count=300)]
    monkeypatch.setattr(runtime.discovery, "search", search)
    result = asyncio.run(runtime.scan(owner))
    assert len(result["runs"]) == 1
    run = result["runs"][0]
    assert run["source_url"].startswith("https://www.tiktok.com/")
    assert run["status"] == "queued" and run["channel_id"] == "mine"
    assert run["config"]["source_urls"] == [] and run["config"]["keywords"] == []
    assert run["config"]["strategy_evidence"]["mode"] == "existing_channel"
    assert run["config"]["source_evidence"]["ranking"]["score"] is not None


@pytest.mark.parametrize("platform,payload,views,likes", [
    ("douyin", {"aweme_list": [{"aweme_id": "123456789", "desc": "科学 #实验", "author": {"uid": "creator"},
        "statistics": {"play_count": 12000, "digg_count": 400}, "create_time": 1793059200}]}, 12000, 400),
    ("tiktok", {"itemList": [{"id": "123456789", "desc": "Science #science", "author": {"uniqueId": "creator"},
        "stats": {"playCount": 18000, "diggCount": 600}, "createTime": 1793059200}]}, 18000, 600),
    ("bilibili", {"data": {"list": [{"bvid": "BV1test123", "title": "科学实验", "owner": {"mid": 123},
        "stat": {"view": 20000, "like": 800}, "pubdate": 1793059200}]}}, 20000, 800),
])
def test_social_page_metadata_retains_real_metrics_and_platform_urls(platform, payload, views, likes):
    samples = SourceDiscovery.public_metadata(platform, payload)
    assert len(samples) == 1
    assert samples[0].platform == platform
    assert samples[0].view_count == views and samples[0].like_count == likes
    assert samples[0].comment_count is None and samples[0].published_at is not None
    assert source_identity(samples[0].url)[0] == platform


def test_social_metadata_without_counters_is_never_labeled_as_measured_growth():
    from universal_video_ai.reup.ranking import rank_source
    candidate = SourceDiscovery.public_metadata("tiktok", {"id": "123456789", "desc": "Science", "author": {"uniqueId": "creator"}})[0]
    assert candidate.view_count is None and candidate.like_count is None
    ranking = rank_source(candidate.to_dict())
    assert ranking["score"] is None and ranking["observed_growth"] is None
    assert ranking["method"] == "public_feed_order" and ranking["confidence"] == "low"


def test_different_niches_in_one_youtube_category_are_not_merged_into_one_strategy(setup):
    store, _, _ = setup
    service = ReupStrategyService(store, YouTube(), Discovery())
    science = video("science0001")
    cooking = video("cooking0001")
    cooking["snippet"].update(title="Cooking recipe", tags=["cooking", "recipe"])
    directions = service._directions([science, cooking], {"27": "Giáo dục"}, profile())
    assert len(directions) == 2
    assert len({row["id"] for row in directions}) == 2
    assert all(row["category_id"] == "27" and row["supporting_videos"] == 1 for row in directions)
    assert not any("cooking" in row["topic"] and "science" in row["topic"] for row in directions)


def test_unrelated_cached_benchmark_is_not_selected_just_because_it_shares_a_category(monkeypatch):
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, "search", lambda *args: [])
    unrelated = Candidate("https://www.youtube.com/watch?v=abcdefghijk", "youtube", "abcdefghijk", "Cooking recipe")
    result = discovery.scan({"platforms": ["youtube"], "search_queries": {"youtube": ["science experiments"]},
                             "seed_candidates": [unrelated.to_dict()]})
    assert result["candidates"] == []


def test_ready_cached_report_repairs_empty_queries_locally_without_rescanning_channel(setup):
    import json
    from universal_video_ai.reup.keywords import KEYWORD_VERSION
    store, owner, other = setup
    youtube = YouTube()
    service = ReupStrategyService(store, youtube, Discovery())
    config = profile(platforms=['douyin', 'tiktok', 'bilibili'])
    direction = {'id': 'selected-direction', 'category_id': '24', 'score': 8, 'content_keywords': ['hot girls', 'trendy', 'china girl'],
                 'evidence': [{'title': 'Fashion and dance trends', 'tags': ['Fashion', 'Dance', 'HotGirls', 'Trendy']}],
                 'search_queries': {p: [] for p in config['platforms']}}
    report = {'query_version': 4, 'mode': 'existing_channel', 'channel': {'channel_id': 'mine', 'title': 'My channel'},
              'generated_at': 1234, 'directions': [direction], 'warnings': []}
    with store._connect() as conn:
        conn.execute("INSERT INTO reup_strategy_state(user_id,channel_id,generation,status,config_key,report,updated_at) VALUES(?,?,?,'ready',?,?,?)",
                     (owner, 'mine', 'existing-generation', service.config_key(config), json.dumps(report), 5678))
    service.keywords.translate = lambda *a: pytest.fail('Reading a cached report must not call translation services')
    fixed = service.refresh_keywords(owner, config)['report']
    assert fixed['keyword_version'] == KEYWORD_VERSION
    assert fixed['generated_at'] == 1234
    assert fixed['directions'][0]['id'] == 'selected-direction'
    assert fixed['directions'][0]['search_queries']['douyin']
    assert fixed['directions'][0]['search_queries']['tiktok']
    assert not youtube.calls
    assert not service.status(other)['report']
    assert service.refresh_keywords(owner, config)['report']['keywords_updated_at'] == fixed['keywords_updated_at']
