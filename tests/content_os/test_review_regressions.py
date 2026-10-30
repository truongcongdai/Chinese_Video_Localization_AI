"""Regression coverage for F41–F45 with fake providers and temporary storage."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from universal_video_ai.content_os.agents.base import BaseAgent
from universal_video_ai.content_os.artifact_store import ArtifactStore
from universal_video_ai.content_os.enums import ApprovalType, ArtifactType
from universal_video_ai.content_os.exceptions import WorkflowError
from universal_video_ai.content_os.pipeline_adapter import PipelineAdapter
from universal_video_ai.content_os.production import execute_production_job
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.content_os.workflow import ContentOSWorkflow, WorkflowConfig
from universal_video_ai.web.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(BaseAgent, "_call_llm", lambda agent, prompt: agent._mock_output())
    store = Store(tmp_path / "review.sqlite3")
    owner = store.create_user("owner", "fake-hash")
    repo = ContentOSRepository(store.db_path)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    project = repo.create_project(owner, None, "Science", "ai_video", "Science", "Explain",
        "youtube_shorts", 45, "vi", "trend_decode", "documentary", "", "", False, "")
    run = repo.create_run(project.id, owner)
    workflow = ContentOSWorkflow(repo, artifacts, WorkflowConfig(auto_approve=False))
    adapter = PipelineAdapter(repo, artifacts, store.db_path)
    return SimpleNamespace(store=store, owner=owner, repo=repo, artifacts=artifacts,
                           project=project, run=run, workflow=workflow, adapter=adapter)


def prepare(s):
    s.workflow.start_run(s.run.id, s.owner)
    s.workflow.submit_approval(s.run.id, s.owner, ApprovalType.SCRIPT, "approved")
    return s.adapter.create_job_from_run(s.run.id, s.owner)


def approve_storyboard(s):
    s.workflow.storyboard_manager.approve_storyboard(s.run.id, s.owner, s.owner)
    s.workflow.submit_approval(s.run.id, s.owner, ApprovalType.STORYBOARD, "approved")


def fake_media(s, monkeypatch, tmp_path, *, valid=True):
    captured = []
    monkeypatch.setattr(s.workflow, "_plan_assets", lambda *args: captured.append(args[-1]["storyboard"]) or {})
    for name in ("_resolve_assets", "_generate_voice", "_generate_subtitles"):
        monkeypatch.setattr(s.workflow, name, lambda *args: {})
    monkeypatch.setattr(s.workflow, "_build_timeline", lambda *args: {"total_duration": 45})
    output = tmp_path / "fake.mp4"
    output.write_bytes(b"fake transport output; validator is injected")
    monkeypatch.setattr(s.workflow, "_render_video", lambda *args: {"output_path": str(output)})
    monkeypatch.setattr(s.workflow.mp4_validator, "validate", lambda **kwargs: SimpleNamespace(
        status=SimpleNamespace(value="valid" if valid else "invalid"), file_size_bytes=100,
        duration_seconds=45, resolution="1080x1920", video_codec="h264", audio_codec="aac",
        issues=[] if valid else ["Invalid video stream"], warnings=[]))
    return captured


def test_f41_waiting_approval_is_not_completed_or_restarted(setup):
    s = setup
    result = s.workflow.start_run(s.run.id, s.owner)
    saved = s.repo.get_run(s.run.id, s.owner)
    assert result["status"] == saved.status == "awaiting_approval"
    assert saved.completed_at is None and saved.progress_percent < 100
    before = len(s.repo.list_steps(s.run.id))
    assert s.workflow.resume_run(s.run.id, s.owner)["status"] == "awaiting_approval"
    assert len(s.repo.list_steps(s.run.id)) == before
    s.workflow.submit_approval(s.run.id, s.owner, ApprovalType.SCRIPT, "approved")
    saved = s.repo.get_run(s.run.id, s.owner)
    assert saved.status == "approved" and saved.current_stage == "ready_for_localization"
    assert saved.completed_at is None


def test_f42_revision_is_versioned_approved_and_handed_to_job(setup, monkeypatch):
    s = setup
    revised = copy.deepcopy(s.workflow.agents["script_writer"]._mock_output())
    revised["title_options"] = ["Approved revision"]
    revised["segments"][0]["narration"] = "Revised narration"
    audits = iter([{"decision": "PASS_WITH_FIXES"}, {"decision": "PASS"}])
    monkeypatch.setattr(s.workflow.agents["content_audit"], "execute", lambda ctx: next(audits))
    monkeypatch.setattr(s.workflow.agents["script_reviser"], "execute", lambda ctx: {"revised_script": revised})
    job_id = prepare(s)
    script = s.artifacts.read(s.owner, s.project.id, s.run.id, ArtifactType.SCRIPT)
    assert script["version"] == 2 and script["data"] == revised
    assert s.store.get_job(job_id).title == "Approved revision"
    assert s.workflow.production_context(s.run.id, s.owner)["script"] == revised
    records = [a for a in s.repo.list_artifacts(s.run.id) if a.artifact_type == "script"]
    assert [a.version for a in records] == [1, 2]
    assert all(a.checksum != "mock_checksum" for a in records)


@pytest.mark.parametrize("change", ["new_version", "changed_bytes"])
def test_f42_handoff_rejects_changes_after_approval(setup, change):
    s = setup
    prepare(s)
    info = s.artifacts.describe(s.owner, s.project.id, s.run.id, ArtifactType.SCRIPT)
    if change == "new_version":
        s.artifacts.write(s.owner, s.project.id, s.run.id, ArtifactType.SCRIPT, {"title_options": ["unapproved"]}, "editor")
    else:
        from pathlib import Path
        path = Path(info["path"])
        data = json.loads(path.read_text())
        data["data"]["hook"] = "unapproved edit"
        path.write_text(json.dumps(data))
    with pytest.raises(WorkflowError, match="approve the current version"):
        s.adapter.create_job_from_run(s.run.id, s.owner)


def test_f44_manual_gate_resumes_saved_edited_storyboard_after_restart(setup, monkeypatch, tmp_path):
    s = setup
    job_id = prepare(s)
    monkeypatch.setattr(s.workflow, "_plan_assets", lambda *args: pytest.fail("must wait for human approval"))
    result = execute_production_job(s.workflow, s.store, s.run.id, s.owner, job_id)
    assert result["stage"] == "awaiting_storyboard_approval"
    assert s.store.get_job(job_id).status == "review"
    assert s.repo.get_run(s.run.id, s.owner).status == "awaiting_approval"
    assert s.repo.get_latest_approval(s.run.id, "storyboard") is None
    s.workflow.storyboard_manager.update_scene(s.run.id, s.owner, "scene_1", {"visual_instruction": "Human edited scene"})
    s.workflow = ContentOSWorkflow(s.repo, s.artifacts, WorkflowConfig(auto_approve=False))
    approve_storyboard(s)
    captured = fake_media(s, monkeypatch, tmp_path)
    monkeypatch.setattr(s.workflow, "_generate_storyboard", lambda *args: pytest.fail("must not regenerate storyboard"))
    assert s.adapter.create_job_from_run(s.run.id, s.owner) == job_id
    result = execute_production_job(s.workflow, s.store, s.run.id, s.owner, job_id)
    assert result["status"] == "completed"
    assert captured[0]["scenes"][0]["visual_instruction"] == "Human edited scene"
    saved = s.repo.get_run(s.run.id, s.owner)
    assert saved.status == "completed" and saved.progress_percent == 100 and saved.completed_at
    assert s.store.get_job(job_id).status == "done"


@pytest.mark.parametrize("failure", ["validation", "render", "missing_output"])
def test_f43_f45_invalid_or_failed_production_never_marks_job_done(setup, monkeypatch, tmp_path, failure):
    s = setup
    job_id = prepare(s)
    execute_production_job(s.workflow, s.store, s.run.id, s.owner, job_id)
    approve_storyboard(s)
    fake_media(s, monkeypatch, tmp_path, valid=failure != "validation")
    if failure == "render":
        def fail(*args):
            raise OSError("render failed")
        monkeypatch.setattr(s.workflow, "_render_video", fail)
    if failure == "missing_output":
        monkeypatch.setattr(s.workflow, "_render_video", lambda *args: {"output_path": str(tmp_path / "absent.mp4")})
    with pytest.raises((WorkflowError, OSError)):
        execute_production_job(s.workflow, s.store, s.run.id, s.owner, job_id)
    saved = s.repo.get_run(s.run.id, s.owner)
    assert saved.status == "failed" and saved.completed_at and saved.error_json
    assert saved.current_stage != "completed"
    assert s.store.get_job(job_id).status == "error"
    if failure == "validation":
        report = s.artifacts.read(s.owner, s.project.id, s.run.id, ArtifactType.OUTPUT_VALIDATION)
        assert report["data"]["valid"] is False


def test_f44_old_storyboard_approval_cannot_authorize_an_edit(setup, monkeypatch, tmp_path):
    s = setup
    job_id = prepare(s)
    execute_production_job(s.workflow, s.store, s.run.id, s.owner, job_id)
    approve_storyboard(s)
    s.workflow.storyboard_manager.update_scene(s.run.id, s.owner, "scene_1", {"narration_text": "Changed after approval"})
    monkeypatch.setattr(s.workflow, "_plan_assets", lambda *args: pytest.fail("stale approval"))
    with pytest.raises(WorkflowError, match="approve the current version"):
        execute_production_job(s.workflow, s.store, s.run.id, s.owner, job_id)


def test_production_claim_is_shared_between_repository_instances(setup):
    s = setup
    prepare(s)
    assert s.repo.claim_production(s.run.id, s.owner)
    assert not ContentOSRepository(s.store.db_path).claim_production(s.run.id, s.owner)
