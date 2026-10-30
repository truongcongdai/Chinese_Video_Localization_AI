"""F46–F48: fair scheduling, truthful duration gates and explicit new attempts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from universal_video_ai.reup.discovery import Candidate, SourceDiscovery
from universal_video_ai.reup.service import ReupRepository
from universal_video_ai.web.store import Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "reup.sqlite3")
    owner = store.create_user("owner", "fake-hash")
    config = {"channel_id": "mine", "timezone": "Asia/Bangkok", "daily_limit": 100,
              "automation": "assisted", "platforms": ["douyin"], "topic": "science"}
    return store, owner, ReupRepository(store), config


def enqueue(s, index, **changes):
    store, owner, repo, config = s
    row = repo.enqueue(owner, {"url": f"https://www.douyin.com/video/{index}"}, config)
    if changes:
        row = repo.update(owner, row["id"], **changes)
    return row


def test_f46_approval_waits_future_schedules_and_disabled_runs_do_not_fill_batch(setup):
    store, owner, repo, config = setup
    for i in range(20):
        enqueue(setup, i, status="ready")
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    enqueue(setup, 20, status="scheduled", refs={"schedule_utc": future})
    enqueue(setup, 21, status="queued", refs={"trigger": "automatic"})
    wanted = enqueue(setup, 22, status="queued", refs={"trigger": "manual"})
    assert repo.active() == [(owner, wanted["id"])]


def test_f46_polling_rotates_across_pending_work_and_survives_restart(setup):
    _, owner, repo, _ = setup
    rows = [enqueue(setup, i, status="localizing") for i in range(45)]
    at = datetime.now().timestamp()
    first = repo.active(at=at)
    second = ReupRepository(setup[0]).active(at=at)
    third = repo.active(at=at)
    assert len(first) == len(second) == 20 and len(third) == 5
    assert len(set(first + second + third)) == 45
    assert repo.active(at=at) == []
    assert len(repo.active(at=at + 31)) == 20


@pytest.mark.parametrize("platform,node,seconds", [
    ("douyin", {"aweme_id": "123", "desc": "science", "video": {"duration": 600000}}, 600),
    ("douyin", {"aweme_id": "123", "desc": "science", "duration": 45000}, 45),
    ("tiktok", {"id": "123", "desc": "science", "author": {"uniqueId": "creator"}, "video": {"duration": 45}}, 45),
    ("bilibili", {"bvid": "BV123", "title": "science", "duration": "12:30"}, 750),
    ("bilibili", {"bvid": "BV123", "title": "science", "duration": 45}, 45),
])
def test_f47_platform_duration_units_are_normalized(platform, node, seconds):
    assert SourceDiscovery.public_metadata(platform, node)[0].duration == seconds


def test_f47_duration_limits_reject_long_and_unknown_automatic_sources(monkeypatch):
    discovery = SourceDiscovery()
    rows = [Candidate(f"https://www.douyin.com/video/{index}", "douyin", str(index), "science", duration)
            for index, duration in enumerate((600, 45, 0, 5))]
    monkeypatch.setattr(discovery, "search", lambda *args: rows)
    result = discovery.scan({"platforms": ["douyin"], "topic": "science", "automation": "auto",
                             "min_seconds": 30, "max_seconds": 60})
    assert [row["duration"] for row in result["candidates"]] == [45]
    assert any("unknown duration" in warning for warning in result["warnings"])


def test_f47_assisted_discovery_keeps_unknown_duration_for_manual_selection(setup, monkeypatch, tmp_path):
    from universal_video_ai.web.reup_router import ReupRuntime
    store, owner, _, config = setup
    async def submit(*args):
        pytest.fail("must not submit unknown length automatically")
    runtime = ReupRuntime(store, submit, tmp_path)
    runtime.repository.save_profile(owner, config)
    monkeypatch.setattr(runtime.discovery, "scan", lambda cfg: {"candidates": [
        Candidate("https://www.douyin.com/video/123", "douyin", "123", "science").to_dict()], "warnings": []})
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", direct)
    result = asyncio.run(runtime.scan(owner))
    assert result["runs"][0]["status"] == "discovered"
    assert any("unknown" in warning for warning in result["warnings"])


def test_f48_explicit_retry_resets_cancelled_attempt_but_not_automatic_discovery(setup):
    _, owner, repo, config = setup
    row = enqueue(setup, 123, status="cancelled", job_id="cancelled-job", approved=True, refs={"qc": {"ready": True}})
    old_id = repo.localization_id(row)
    assert repo.enqueue(owner, {"url": row["source_url"]}, config)["status"] == "cancelled"
    restarted = repo.enqueue(owner, {"url": row["source_url"]}, config, restart_cancelled=True)
    assert restarted["status"] == "discovered" and restarted["attempt"] == 2
    assert not restarted["job_id"] and not restarted["approved"] and restarted["refs"] == {}
    assert repo.localization_id(restarted) != old_id
    assert repo.enqueue(owner, {"url": row["source_url"]}, config, restart_cancelled=True)["attempt"] == 2


@pytest.mark.parametrize("remote", [{"external_video_id": "remote"}, {"upload_session_uri": "opaque-session"}])
def test_f48_never_resets_a_source_with_possible_remote_upload(setup, remote):
    _, owner, repo, config = setup
    row = enqueue(setup, 123, status="cancelled", refs=remote)
    saved = repo.enqueue(owner, {"url": row["source_url"]}, config, restart_cancelled=True)
    assert saved["status"] == "cancelled" and saved["attempt"] == 1


def test_f48_new_attempt_obeys_daily_limit(setup):
    _, owner, repo, config = setup
    cancelled = enqueue(setup, 123, status="cancelled")
    enqueue(setup, 124, status="queued")
    assert repo.enqueue(owner, {"url": cancelled["source_url"]}, {**config, "daily_limit": 1}, restart_cancelled=True) is None
