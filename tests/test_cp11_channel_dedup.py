from __future__ import annotations

import sqlite3
import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from universal_video_ai.channel_agent.channel_sources import (
    ChannelSourceError,
    ChannelSourceRegistry,
    canonical_source_url,
    youtube_video_id,
)
from universal_video_ai.web.store import Store


def setup_registry(tmp_path):
    store = Store(tmp_path / "sources.sqlite3")
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    return store, owner, foreign, ChannelSourceRegistry(store)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc123XYZ_0&t=10",
    "https://youtu.be/abc123XYZ_0?si=test",
    "https://youtube.com/shorts/abc123XYZ_0",
    "https://youtube.com/live/abc123XYZ_0?feature=share",
    "https://youtube.com/embed/abc123XYZ_0",
])
def test_youtube_url_variations_have_one_canonical_identity(url):
    assert youtube_video_id(url) == "abc123XYZ_0"
    assert canonical_source_url("youtube", "abc123XYZ_0", url) == (
        "https://www.youtube.com/watch?v=abc123XYZ_0"
    )


def test_same_video_twice_and_later_scan_stays_one_logical_row(tmp_path):
    _, owner, _, registry = setup_registry(tmp_path)
    first = registry.discover(owner, platform="youtube", channel_id="chan", channel_url="channel", videos=[
        {"video_id": "abc123XYZ_0", "source_url": "https://youtu.be/abc123XYZ_0", "title": "A"},
        {"video_id": "abc123XYZ_0", "source_url": "https://youtube.com/watch?v=abc123XYZ_0", "title": "A"},
    ])
    later = registry.discover(owner, platform="youtube", channel_id="chan", channel_url="channel", videos=[
        {"source_url": "https://youtube.com/shorts/abc123XYZ_0", "title": "Updated"},
    ])
    assert len(first) == len(later) == 1
    assert first[0]["id"] == later[0]["id"]
    assert len(registry.list_visible(owner)) == 1


def test_duplicate_title_different_ids_and_cross_owner_are_distinct(tmp_path):
    _, owner, foreign, registry = setup_registry(tmp_path)
    videos = [
        {"video_id": "video_A1", "source_url": "https://youtu.be/video_A1", "title": "Same"},
        {"video_id": "video_B2", "source_url": "https://youtu.be/video_B2", "title": "Same"},
    ]
    assert len(registry.discover(owner, platform="youtube", channel_id="c", channel_url="u", videos=videos)) == 2
    foreign_row = registry.discover(
        foreign, platform="youtube", channel_id="c", channel_url="u", videos=[videos[0]]
    )[0]
    assert len(registry.list_visible(owner)) == 2
    assert len(registry.list_visible(foreign)) == 1
    assert foreign_row["user_id"] == foreign


def test_preflight_classifies_all_states_and_only_new_retryable_are_eligible(tmp_path):
    store, owner, _, registry = setup_registry(tmp_path)
    videos = [{"video_id": f"video_{i}X", "source_url": f"https://youtu.be/video_{i}X"} for i in range(7)]
    rows = registry.discover(owner, platform="youtube", channel_id="c", channel_url="u", videos=videos)
    states = [
        ("QUEUED", "new", "queued", 0),
        ("PROCESSING", "processing", "running", 1),
        ("SUCCESS", "success", "done", 1),
        ("FAILED", "failed", "error", 1),
        ("SKIPPED", "new", "none", 0),
        ("FAILED", "failed", "error", 3),
    ]
    with store._connect() as conn:
        for row, values in zip(rows[1:], states):
            conn.execute(
                "UPDATE channel_source_videos SET status=?,download_status=?,queue_status=?,attempt_count=? WHERE id=?",
                (*values, row["id"]),
            )
    refreshed = registry.list_visible(owner)
    preflight = registry.preflight(refreshed, max_attempts=3)
    assert preflight["discovered"] == 7
    assert preflight["new"] == 1
    assert preflight["already_queued"] == 1
    assert preflight["processing"] == 1
    assert preflight["already_successful"] == 1
    assert preflight["failed_retryable"] == 1
    assert preflight["skipped"] == 1 and preflight["failed"] == 1
    assert len(preflight["eligible"]) == 2


def test_failed_retry_reuses_source_and_job_then_success_compacts_visible_state(tmp_path):
    store, owner, _, registry = setup_registry(tmp_path)
    source = registry.discover(owner, platform="youtube", channel_id="c", channel_url="u", videos=[
        {"video_id": "retry_123", "source_url": "https://youtu.be/retry_123"},
    ])[0]
    job = store.create_job(owner, source["canonical_url"], "vi")
    registry.link_job(owner, source["id"], job.id)
    first = registry.begin_attempt(owner, source["id"], mode="NEW_DOWNLOAD", job_id=job.id)
    registry.finish(owner, source["id"], success=False, job_id=job.id, error="network")
    assert registry.decision(registry.get(owner, source["id"])) == "RETRYABLE_FAILED"
    second = registry.begin_attempt(owner, source["id"], mode="AUTO_RETRY_FAILED", job_id=job.id)
    completed = registry.finish(
        owner, source["id"], success=True, job_id=job.id, local_asset_path="owned.mp4"
    )
    assert first["source_video_id"] == second["source_video_id"] == source["id"]
    assert second["attempt_number"] == 2 and completed["attempt_count"] == 2
    assert completed["status"] == "SUCCESS" and completed["last_error"] is None
    visible = registry.list_visible(owner)
    assert len(visible) == 1 and len(visible[0]["attempts"]) == 2


def test_source_attempts_and_ids_are_owner_scoped(tmp_path):
    _, owner, foreign, registry = setup_registry(tmp_path)
    source = registry.discover(owner, platform="youtube", channel_id="c", channel_url="u", videos=[
        {"video_id": "secret_1", "source_url": "https://youtu.be/secret_1"},
    ])[0]
    with pytest.raises(ChannelSourceError):
        registry.begin_attempt(foreign, source["id"], mode="NEW_DOWNLOAD")
    with pytest.raises(ChannelSourceError):
        registry.attempts(foreign, source["id"])
    assert registry.get(foreign, source["id"]) is None


def test_job_rerun_attempts_preserve_logical_job_and_never_repeat_publish_by_default(tmp_path):
    store, owner, foreign, registry = setup_registry(tmp_path)
    job = store.create_job(owner, "https://youtu.be/rerun_123", "vi")
    first = registry.create_job_attempt(
        owner, job.id, mode="RESTART_FROM_BEGINNING",
        reset_stages=["download", "translation", "render"],
        reused_outputs={"publishing": True},
    )
    second = registry.create_job_attempt(
        owner, job.id, mode="RETRY_FROM_FAILED_STAGE",
        reset_stages=[], reused_outputs={"download": True},
    )
    assert first["job_id"] == second["job_id"] == job.id
    assert [row["attempt_number"] for row in registry.job_attempts(owner, job.id)] == [3, 2]
    assert not first["repeat_publish"] and not second["repeat_publish"]
    assert store.get_job(job.id).execution_attempt == 3
    with pytest.raises(ChannelSourceError):
        registry.job_attempts(foreign, job.id)


def test_legacy_compaction_archives_only_proven_failed_duplicates(tmp_path):
    store, owner, _, registry = setup_registry(tmp_path)
    first = store.create_job(owner, "https://youtu.be/legacy_1", "vi")
    second = store.create_job(owner, "https://youtube.com/watch?v=legacy_1&t=20", "vi")
    success = store.create_job(owner, "https://youtube.com/shorts/legacy_1", "vi")
    unknown = store.create_job(owner, "https://example.invalid/video", "vi")
    store.update_job(first.id, status="error")
    store.update_job(second.id, status="error")
    store.update_job(success.id, status="done")
    store.update_job(unknown.id, status="error")
    result = registry.compact_legacy_failed_jobs(owner)
    assert result["groups"] == 1 and result["archived_failed"] == 2
    visible = {job.id for job in store.list_jobs_for_user(owner)}
    assert success.id in visible and unknown.id in visible
    assert first.id not in visible and second.id not in visible


@pytest.mark.parametrize("job_status,decision", [
    ("queued", "QUEUED"), ("running", "PROCESSING"),
    ("done", "SUCCESS"), ("error", "RETRYABLE_FAILED"),
])
def test_first_post_upgrade_scan_bootstraps_legacy_job_state(tmp_path, job_status, decision):
    store, owner, _, registry = setup_registry(tmp_path)
    video_id = f"legacy_{job_status}"
    job = store.create_job(owner, f"https://youtu.be/{video_id}", "vi")
    store.update_job(job.id, status=job_status)
    row = registry.discover(owner, platform="youtube", channel_id="c", channel_url="u", videos=[
        {"video_id": video_id, "source_url": f"https://youtube.com/watch?v={video_id}"},
    ])[0]
    assert row["logical_job_id"] == job.id
    assert registry.decision(row) == decision
    assert store.get_job(job.id).source_logical_id == row["id"]


def test_old_database_upgrade_and_repeated_registry_init_preserve_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_marker (value TEXT)")
        conn.execute("INSERT INTO legacy_marker VALUES ('keep')")
    store = Store(path)
    ChannelSourceRegistry(store)
    ChannelSourceRegistry(Store(path))
    with store._connect() as conn:
        marker = conn.execute("SELECT value FROM legacy_marker").fetchone()[0]
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert marker == "keep"
    assert {"source_external_video_id", "history_archived", "execution_attempt"}.issubset(columns)
    assert {"channel_source_videos", "channel_source_attempts", "job_execution_attempts"}.issubset(tables)


def test_ui_contract_includes_rerun_attempts_and_batch_summary():
    root = Path(__file__).parents[1]
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    for text in (
        "Run all again", "RESTART_FROM_BEGINNING", "repeat_publish: false",
        "Retry failed stage", "RETRY_FROM_FAILED_STAGE",
        "Attempts:", "downloaded new", "retried failed", "skipped success", "failed again",
    ):
        assert text in js


def test_rerun_api_full_restart_confirmation_idor_and_no_republish(tmp_path, monkeypatch):
    web_app = importlib.import_module("universal_video_ai.web.app")
    store, owner, foreign, registry = setup_registry(tmp_path)
    source = registry.discover(owner, platform="youtube", channel_id="c", channel_url="u", videos=[
        {"video_id": "api_rerun_1", "source_url": "https://youtu.be/api_rerun_1"},
    ])[0]
    job = store.create_job(owner, source["canonical_url"], "vi")
    registry.link_job(owner, source["id"], job.id)
    store.update_job(
        job.id, status="done", title="Approved title", source_video_path="source.mp4",
        final_video_path="final.mp4", segments_json="[]",
    )
    monkeypatch.setattr(web_app, "store", store)
    monkeypatch.setattr(web_app, "channel_source_registry", registry)
    monkeypatch.setattr(web_app, "JOB_COST_CREDITS", 0)
    monkeypatch.setattr(web_app, "_running_tasks", {})
    async def no_work(job_id):
        return None
    monkeypatch.setattr(web_app, "_run_job", no_work)

    with pytest.raises(HTTPException) as confirmation:
        asyncio.run(web_app.rerun_full_pipeline(
            job.id, web_app.PipelineRerunBody(mode="RESTART_FROM_BEGINNING"), user_id=owner,
        ))
    assert confirmation.value.status_code == 409
    with pytest.raises(HTTPException) as idor:
        asyncio.run(web_app.rerun_full_pipeline(
            job.id, web_app.PipelineRerunBody(mode="RESTART_FROM_BEGINNING", confirm_completed=True),
            user_id=foreign,
        ))
    assert idor.value.status_code == 404

    result = asyncio.run(web_app.rerun_full_pipeline(
        job.id,
        web_app.PipelineRerunBody(mode="RESTART_FROM_BEGINNING", confirm_completed=True),
        user_id=owner,
    ))
    current = store.get_job(job.id)
    assert result["job"]["id"] == job.id and result["remote_publish_repeated"] is False
    assert current.title == "Approved title" and current.source_external_video_id == "api_rerun_1"
    assert current.source_video_path is None and current.final_video_path is None
    assert len(registry.list_visible(owner)) == 1


def test_rerun_api_retry_from_failed_preserves_valid_outputs(tmp_path, monkeypatch):
    web_app = importlib.import_module("universal_video_ai.web.app")
    store, owner, _, registry = setup_registry(tmp_path)
    job = store.create_job(owner, "https://youtu.be/api_retry_1", "vi")
    store.update_job(
        job.id, status="error", source_video_path="source.mp4",
        source_segments_json='[{"text":"source"}]', segments_json='[{"text":"translated"}]',
    )
    monkeypatch.setattr(web_app, "store", store)
    monkeypatch.setattr(web_app, "channel_source_registry", registry)
    monkeypatch.setattr(web_app, "JOB_COST_CREDITS", 0)
    monkeypatch.setattr(web_app, "_running_tasks", {})
    async def no_work(job_id):
        return None
    monkeypatch.setattr(web_app, "_run_job", no_work)
    result = asyncio.run(web_app.rerun_full_pipeline(
        job.id, web_app.PipelineRerunBody(mode="RETRY_FROM_FAILED_STAGE"), user_id=owner,
    ))
    current = store.get_job(job.id)
    assert result["reused"]["source_video"] and result["reused"]["translation"]
    assert current.source_video_path == "source.mp4"
    assert current.segments_json == '[{"text":"translated"}]'
    assert registry.job_attempts(owner, job.id)[0]["mode"] == "RETRY_FROM_FAILED_STAGE"
