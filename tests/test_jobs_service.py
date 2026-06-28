# tests/test_jobs_service.py
from pathlib import Path
import pytest
import time

from universal_video_ai.jobs.service import JobService
from universal_video_ai.jobs.models import Job, JobStatus, JobConfig


def test_job_creation():
    service = JobService()
    config = JobConfig(url="http://example.com/video", output_dir=Path("/tmp"))
    job = service.create_job(config)

    assert job.job_id is not None
    assert job.status == JobStatus.PENDING
    assert job.progress == 0.0


def test_job_list():
    service = JobService()
    config = JobConfig(url="http://example.com/video", output_dir=Path("/tmp"))

    job1 = service.create_job(config)
    job2 = service.create_job(config)

    jobs = service.list_jobs()
    assert len(jobs) == 2
    assert job1.job_id in [j.job_id for j in jobs]


def test_job_update():
    service = JobService()
    config = JobConfig(url="http://example.com/video", output_dir=Path("/tmp"))
    job = service.create_job(config)

    updated = service.update_job(job.job_id, status=JobStatus.RUNNING, progress=0.5)
    assert updated.status == JobStatus.RUNNING
    assert updated.progress == 0.5


def test_job_async_execution(tmp_path: Path):
    service = JobService()
    config = JobConfig(url="http://example.com/video", output_dir=tmp_path)
    job = service.create_job(config)

    result_file = tmp_path / "result.txt"

    def fake_work(job_id: str):
        result_file.write_text("done")
        return result_file

    thread = service.run_job_async(job.job_id, fake_work)
    thread.join(timeout=2)

    final_job = service.get_job(job.job_id)
    assert final_job.status == JobStatus.COMPLETED
    assert result_file.exists()


def test_job_async_failure(tmp_path: Path):
    service = JobService()
    config = JobConfig(url="http://example.com/video", output_dir=tmp_path)
    job = service.create_job(config)

    def failing_work(job_id: str):
        raise RuntimeError("test error")

    thread = service.run_job_async(job.job_id, failing_work)
    thread.join(timeout=2)

    final_job = service.get_job(job.job_id)
    assert final_job.status == JobStatus.FAILED
    assert "test error" in final_job.error