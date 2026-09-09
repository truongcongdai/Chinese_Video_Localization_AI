"""Selected reruns preserve ownership, queue limits and existing job identity."""
import asyncio
import importlib

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from universal_video_ai.channel_agent.channel_sources import ChannelSourceRegistry
from universal_video_ai.web.store import Store


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    app = importlib.import_module("universal_video_ai.web.app")
    store = Store(tmp_path / "bulk.sqlite3")
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    monkeypatch.setattr(app, "store", store)
    monkeypatch.setattr(app, "channel_source_registry", ChannelSourceRegistry(store))
    monkeypatch.setattr(app, "JOB_COST_CREDITS", 0)
    monkeypatch.setattr(app, "_running_tasks", {})
    return app, store, owner, foreign


def make_job(store, owner, status, **fields):
    job = store.create_job(owner, "https://youtu.be/test_video", "vi")
    store.update_job(job.id, status=status, **fields)
    return job.id


def test_retry_all_crosses_history_limit_without_touching_other_jobs(runtime, monkeypatch):
    app, store, owner, foreign = runtime
    ids = [make_job(store, owner, "error" if i % 2 else "cancelled") for i in range(205)]
    excluded = [make_job(store, foreign, "error"), make_job(store, owner, "review"),
                make_job(store, owner, "running"), make_job(store, owner, "done"),
                make_job(store, owner, "error", source_language="content_os"),
                make_job(store, owner, "error", history_archived=1)]
    scheduled = []
    monkeypatch.setattr(app, "_schedule_job_rerun", lambda job: scheduled.append(job.id))
    result = asyncio.run(app.retry_all_incomplete_jobs(owner))
    assert set(result["queued"]) == set(scheduled) == set(ids)
    assert not result["errors"]
    assert all(store.get_job(job_id).status != "queued" for job_id in excluded)
    assert asyncio.run(app.retry_all_incomplete_jobs(owner))["queued"] == []


def test_bulk_enqueues_all_selected_stopped_jobs_without_waiting(runtime, monkeypatch):
    app, store, owner, _ = runtime
    eligible = [make_job(store, owner, status) for status in ("cancelled", "error", "review")]
    unselected = make_job(store, owner, "cancelled")
    ineligible = [make_job(store, owner, status) for status in ("running", "queued", "done")]
    content_os = make_job(store, owner, "error", source_language="content_os")
    started = []

    async def scenario():
        finish = asyncio.Event()
        async def worker(job_id):
            started.append(job_id)
            await finish.wait()
        monkeypatch.setattr(app, "_run_job", worker)
        result = await asyncio.wait_for(app.rerun_selected_jobs(
            app.BulkRerunBody(job_ids=eligible + eligible[:1] + ineligible + [content_os]),
            user_id=owner,
        ), timeout=2)
        assert result["queued"] == eligible
        assert len(result["skipped"]) == 4
        assert not result["errors"]
        assert set(started) == set(eligible)
        assert all(not task.done() for task in app._running_tasks.values())
        repeated = await app.rerun_selected_jobs(app.BulkRerunBody(job_ids=eligible), user_id=owner)
        assert not repeated["queued"] and len(repeated["skipped"]) == 3
        finish.set()
        await asyncio.gather(*app._running_tasks.values())
    asyncio.run(scenario())
    assert store.get_job(unselected).status == "cancelled"
    for job_id in eligible:
        attempts = app.channel_source_registry.job_attempts(owner, job_id)
        assert len(attempts) == 1 and not attempts[0]["repeat_publish"]
        assert attempts[0]["mode"] == "RESTART_FROM_BEGINNING"


def test_bulk_checks_entire_selection_ownership_before_mutation(runtime):
    app, store, owner, foreign = runtime
    owned = make_job(store, owner, "cancelled")
    other = make_job(store, foreign, "cancelled")
    with pytest.raises(HTTPException) as error:
        asyncio.run(app.rerun_selected_jobs(app.BulkRerunBody(job_ids=[owned, other]), user_id=owner))
    assert error.value.status_code == 404
    assert store.get_job(owned).status == "cancelled"
    assert app.channel_source_registry.job_attempts(owner, owned) == []


def test_bulk_reports_credit_failure_and_keeps_unscheduled_job(runtime, monkeypatch):
    app, store, owner, _ = runtime
    ids = [make_job(store, owner, "cancelled") for _ in range(2)]
    store.adjust_credits(owner, 1 - store.get_user_by_id(owner)["credits"])
    monkeypatch.setattr(app, "JOB_COST_CREDITS", 1)
    scheduled = []
    monkeypatch.setattr(app, "_schedule_job_rerun", lambda job: scheduled.append(job.id))
    result = asyncio.run(app.rerun_selected_jobs(app.BulkRerunBody(job_ids=ids), user_id=owner))
    assert scheduled == result["queued"] == ids[:1]
    assert result["errors"][0]["job_id"] == ids[1]
    assert result["errors"][0]["status_code"] == 402
    assert store.get_job(ids[1]).status == "cancelled"
    assert store.get_user_by_id(owner)["credits"] == 0


@pytest.mark.parametrize("status", ["running", "queued"])
def test_single_rerun_rejects_active_job_without_new_attempt(runtime, status):
    app, store, owner, _ = runtime
    job_id = make_job(store, owner, status)
    with pytest.raises(HTTPException) as error:
        asyncio.run(app.rerun_full_pipeline(job_id, app.PipelineRerunBody(
            mode="RESTART_FROM_BEGINNING"), user_id=owner))
    assert error.value.status_code == 409
    assert app.channel_source_registry.job_attempts(owner, job_id) == []


def test_stopping_worker_cannot_be_replaced_while_still_active(runtime, monkeypatch):
    app, store, owner, _ = runtime
    job_id = make_job(store, owner, "cancelled")
    async def scenario():
        pending = asyncio.create_task(asyncio.sleep(60))
        app._running_tasks[job_id] = pending
        try:
            result = await app.rerun_selected_jobs(app.BulkRerunBody(job_ids=[job_id]), user_id=owner)
            assert not result["queued"] and result["errors"][0]["status_code"] == 409
            assert app._running_tasks[job_id] is pending and not pending.cancelled()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())
    assert store.get_job(job_id).status == "cancelled"


def test_creator_rerun_uses_creator_worker(runtime, monkeypatch):
    app, store, owner, _ = runtime
    job_id = make_job(store, owner, "cancelled", source_language="creator:stock",
                      source_url="creator:Test topic")
    calls = []
    monkeypatch.setattr(app, "_run_creator_job",
                        lambda job_id, body: calls.append((job_id, body.topic)))
    async def scenario():
        await app.rerun_selected_jobs(app.BulkRerunBody(job_ids=[job_id]), user_id=owner)
        await asyncio.gather(*app._running_tasks.values())
    asyncio.run(scenario())
    assert calls == [(job_id, "Test topic")]


def test_bulk_selection_must_be_nonempty_and_bounded(runtime):
    app, *_ = runtime
    for ids in ([], ["id"] * 101):
        with pytest.raises(ValidationError):
            app.BulkRerunBody(job_ids=ids)


def test_superseded_worker_cannot_finish_or_continue_restarted_job(runtime):
    app, store, owner, _ = runtime
    job_id = make_job(store, owner, "cancelled")
    old_job = store.get_job(job_id)
    store.update_job(job_id, status="queued", execution_attempt=old_job.execution_attempt + 1)
    assert app._is_job_cancelled(job_id, old_job.execution_attempt)
    assert not app._is_job_cancelled(job_id, old_job.execution_attempt + 1)
    app._finish_job_from_result(job_id, old_job, object())
    assert store.get_job(job_id).status == "queued"
