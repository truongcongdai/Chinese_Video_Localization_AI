from pathlib import Path

from universal_video_ai.web.store import Store


def test_search_jobs_for_user_filters_by_status(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    user_id = store.create_user("user", "hash")
    done = store.create_job(user_id, "https://example.com/done", "vi")
    failed = store.create_job(user_id, "https://example.com/error", "vi")
    store.update_job(done.id, status="done", title="Finished video")
    store.update_job(failed.id, status="error", title="Failed video")

    result = store.search_jobs_for_user(user_id, status="done")

    assert [job.id for job in result] == [done.id]
