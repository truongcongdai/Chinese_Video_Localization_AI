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


def test_create_job_accepts_and_persists_remix_settings(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    user_id = store.create_user("user", "hash")

    job = store.create_job(
        user_id,
        "https://example.com/remix",
        "vi",
        remix_enabled=True,
        remix_platforms=["youtube_long", "facebook_long"],
        remix_goal="education",
        remix_strength="strong",
        subtitle_offset_seconds=-0.2,
    )

    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.remix_enabled == 1
    assert loaded.remix_goal == "education"
    assert loaded.remix_strength == "strong"
    assert loaded.subtitle_offset_seconds == -0.2
    assert loaded.to_dict()["remix_platforms"] == ["youtube_long", "facebook_long"]

    store.update_job(job.id, status="error", error="failed", title="stale")
    retry = store.retry_job(job.id, user_id)
    assert retry is not None
    assert retry.id == job.id
    assert retry.status == "queued"
    assert retry.error is None
    assert retry.title is None
    assert len(store.list_jobs_for_user(user_id)) == 1
    assert retry.remix_enabled == 1
    assert retry.subtitle_offset_seconds == -0.2
    assert retry.to_dict()["remix_platforms"] == ["youtube_long", "facebook_long"]


def test_create_job_persists_audio_settings(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    user_id = store.create_user("user", "hash")

    job = store.create_job(
        user_id,
        "https://example.com/audio",
        "vi",
        keep_original_audio=1,
        background_music_strategy="none",
    )

    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.keep_original_audio == 1
    assert loaded.background_music_strategy == "none"

    store.update_job(job.id, status="error")
    retry = store.retry_job(job.id, user_id)
    assert retry is not None
    assert retry.keep_original_audio == 1
    assert retry.background_music_strategy == "none"


def test_retry_is_atomic_and_does_not_duplicate_history(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    user_id = store.create_user("user", "hash")
    job = store.create_job(user_id, "https://example.com/retry", "vi")
    store.update_job(
        job.id,
        status="error",
        error="boom",
        final_video_path="stale.mp4",
        segments_json='[{"text":"stale"}]',
    )

    first = store.retry_job(job.id, user_id)
    second = store.retry_job(job.id, user_id)

    assert first is not None
    assert first.id == job.id
    assert first.final_video_path is None
    assert first.segments_json is None
    assert second is None
    assert [item.id for item in store.list_jobs_for_user(user_id)] == [job.id]


def test_content_os_job_is_flagged_for_frontend(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    user_id = store.create_user("user", "hash")

    job = store.create_job(
        user_id,
        "content_os://generated_script",
        "vi",
        source_language="content_os:12",
    )

    loaded = store.get_job(job.id)
    assert loaded is not None
    assert loaded.to_dict()["is_content_os"] is True
