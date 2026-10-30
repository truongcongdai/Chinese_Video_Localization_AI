"""Synchronize Content OS production checkpoints with their linked web job."""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from .exceptions import WorkflowError
from .workflow import ContentOSWorkflow


def execute_production_job(workflow: ContentOSWorkflow, store, run_id: int,
                           user_id: int, job_id: str) -> dict:
    """Run one production continuation; persist matching job and run outcomes."""
    job = store.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise WorkflowError("Linked production job not found")
    if job.status not in {"queued", "review", "error"}:
        raise WorkflowError("Linked production job is not ready for execution")
    owner = "content-os:" + uuid.uuid4().hex
    if not store.claim_job_execution(job_id, owner, reserved_owner="", expected_status=job.status):
        raise WorkflowError("Another worker owns this production job")
    with store.background_execution("job", job_id, owner):
        return _execute_claimed_production(workflow, store, run_id, user_id, job_id)


def _execute_claimed_production(workflow, store, run_id: int, user_id: int, job_id: str) -> dict:
    try:
        store.update_job(job_id, status="running", error=None, progress_note="Content OS đang dựng video...")
        context = workflow.production_context(run_id, user_id)
        result = workflow._execute_production_stages(run_id, user_id, context)
        if result["status"] == "awaiting_approval":
            store.update_job(job_id, status="review", error=None, progress_note="Chờ phê duyệt storyboard")
            return result
        output = result.get("output_path")
        if result["status"] != "completed" or result.get("validation", {}).get("valid") is not True or not output or not Path(output).is_file():
            raise WorkflowError("Production did not return a validated output file")
        store.update_job(job_id, final_video_path=output, status="done", error=None, progress_note="Hoàn tất")
        return result
    except Exception as exc:
        workflow.repository.update_run(run_id, user_id, status="failed", completed_at=time.time(),
            error_json=json.dumps({"error": str(exc), "type": type(exc).__name__}))
        store.update_job(job_id, status="error", error=str(exc), progress_note="Content OS render lỗi")
        raise
