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

    retry = store.retry_job(job.id, user_id)
    assert retry is not None
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

    retry = store.retry_job(job.id, user_id)
    assert retry is not None
    assert retry.keep_original_audio == 1
    assert retry.background_music_strategy == "none"


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


def test_registration_code_is_hashed_single_use_and_rate_limited(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    code = store.create_verification_code("Member@Example.com", "register")

    with store._connect() as conn:
        row = conn.execute("SELECT * FROM verification_codes").fetchone()
    assert row["code_hash"] != code
    assert row["identifier"] == "member@example.com"

    success, error = store.verify_code("member@example.com", code, "register")
    assert success is True
    assert error is None
    assert store.verify_code("member@example.com", code, "register")[0] is False

    store.create_verification_code("other@example.com", "register")
    try:
        store.create_verification_code("other@example.com", "register")
    except ValueError as exc:
        assert "Vui lòng chờ" in str(exc)
    else:
        raise AssertionError("OTP resend cooldown was not enforced")


def test_registration_device_uses_device_token_not_shared_network_fingerprint(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    first_user = store.create_user("first-device", "hash")
    second_user = store.create_user("second-device", "hash")
    shared_fingerprint = "same-office-network"

    assert store.claim_registration_device(first_user, "device-one", shared_fingerprint) is True
    assert store.registration_device_owner("device-one", shared_fingerprint) == first_user
    assert store.registration_device_owner("device-two", shared_fingerprint) is None
    assert store.claim_registration_device(second_user, "device-two", shared_fingerprint) is True
    assert store.claim_registration_device(second_user, "device-one", "different") is False
    assert store.release_registration_devices(first_user) == 1
    assert store.registration_device_owner("device-one") is None


def test_local_user_keeps_stable_central_account_id(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    local_id = store.create_user("central-member", "hash", central_user_id=814)

    assert store.central_user_id(local_id) == 814

    store.set_central_user_id(local_id, 912)
    assert store.central_user_id(local_id) == 912


def test_referral_bonus_is_atomic_and_only_awarded_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "referrals.sqlite3")
    referrer_id = store.create_user("referrer", "hash", credits=15)
    invitee_id = store.create_user(
        "invitee", "hash", credits=15, referred_by_user_id=referrer_id,
    )

    assert store.award_referral_bonus(invitee_id, referrer_id, 5) is True
    assert store.get_user_by_id(referrer_id)["credits"] == 20
    assert store.get_user_by_id(invitee_id)["credits"] == 20

    assert store.award_referral_bonus(invitee_id, referrer_id, 5) is False
    assert store.get_user_by_id(referrer_id)["credits"] == 20
    assert store.get_user_by_id(invitee_id)["credits"] == 20
