import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from universal_video_ai.reup.discovery import source_identity, SourceDiscovery
from universal_video_ai.reup.service import ReupRepository
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.reup_router import ReupProfileBody, ReupRuntime, install_reup_routes
from universal_video_ai.web.store import Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "store.sqlite")
    owner = store.create_user("owner", "hash")
    other = store.create_user("other", "hash")
    return store, owner, other


def config(**overrides):
    return ReupProfileBody(topic="家庭", keywords=["家庭"], rights_confirmed=True,
                           **overrides).model_dump()


def candidate():
    return {"url":"https://youtu.be/abcdefghijk?utm=ignored", "title":"家庭 story"}


def test_enqueue_dedup_is_per_owner_and_target_channel_and_daily_cap(setup):
    store, owner, other = setup
    repo = ReupRepository(store)
    row = repo.enqueue(owner, candidate(), config(channel_id="one"))
    assert repo.enqueue(owner, candidate(), config(channel_id="one"))["id"] == row["id"]
    assert repo.enqueue(owner, {"url":"https://youtu.be/lmnopqrstuv"}, config(channel_id="one")) is None
    assert repo.enqueue(owner, candidate(), config(channel_id="two"))["id"] != row["id"]
    assert repo.enqueue(other, candidate(), config(channel_id="one"))["id"] != row["id"]
    with pytest.raises(LookupError):
        repo.get(other, row["id"])


def test_session_url_redacted_and_checkpoint_survives_restart(setup):
    store, owner, _ = setup
    repo = ReupRepository(store)
    row = repo.enqueue(owner, candidate(), config())
    repo.update(owner, row["id"], refs={"upload_session_uri":"secret-session","external_video_id":"remote"})
    restored = ReupRepository(store)
    assert "upload_session_uri" not in restored.get(owner, row["id"])["refs"]
    assert restored.get(owner, row["id"], private=True)["refs"]["upload_session_uri"] == "secret-session"


def test_reup_calls_existing_localization_and_waits_for_publish_approval(setup, tmp_path, monkeypatch):
    store, owner, _ = setup
    payloads = []
    async def submit(payload, user_id, job_id):
        payloads.append(payload)
        job = store.get_job(job_id) or store.create_job(user_id, payload["url"], "vi", job_id=job_id)
        return job.to_dict()
    runtime = ReupRuntime(store, submit, tmp_path)
    row = runtime.repository.enqueue(owner, candidate(), config())
    runtime.repository.update(owner, row["id"], status="queued")
    localized = asyncio.run(runtime.advance(owner, row["id"]))
    assert localized["status"] == "localizing" and payloads[0]["max_concurrent"] == 1
    assert "creator" not in payloads[0] and payloads[0]["publishing_config"]["enabled"]
    store.update_job(localized["job_id"], status="done")
    monkeypatch.setattr(runtime, "_package", lambda *args: {"video_path":"ready.mp4"})
    assert asyncio.run(runtime.advance(owner, row["id"]))["status"] == "ready"
    monkeypatch.setattr(runtime, "_publish", lambda *args: pytest.fail("must wait for approval"))
    assert asyncio.run(runtime.advance(owner, row["id"]))["status"] == "ready"


def test_profile_discovery_disabled_by_default_and_periodic_claim_is_durable(setup):
    store, owner, _ = setup
    repo = ReupRepository(store)
    repo.save_profile(owner, config())
    assert repo.due_profiles(1000) == []
    repo.save_profile(owner, config(enabled=True, source_urls=[candidate()["url"]]))
    assert len(repo.due_profiles(1000)) == 1
    assert ReupRepository(store).due_profiles(1000) == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://127.0.0.1/watch?v=abcdefghijk",
                                  "https://youtube.com.evil.test/watch?v=abcdefghijk", "https://user:pass@youtu.be/abcdefghijk"])
def test_discovery_rejects_non_platform_urls(url):
    with pytest.raises(ValueError):
        source_identity(url)


def test_platform_url_identity_canonicalization():
    assert source_identity("https://www.youtube.com/shorts/abcdefghijk?feature=share") == source_identity(candidate()["url"])
    assert source_identity("https://www.bilibili.com/video/BV123456?p=2")[1] == "BV123456:p2"


def test_discovery_filters_unrelated_topics_and_negative_keywords(monkeypatch):
    from universal_video_ai.reup.discovery import Candidate
    discovery = SourceDiscovery()
    monkeypatch.setattr(discovery, "search", lambda *args: [
        Candidate("https://youtu.be/abcdefghijk", "youtube", "abcdefghijk", "家庭 story"),
        Candidate("https://youtu.be/lmnopqrstuv", "youtube", "lmnopqrstuv", "unrelated"),
        Candidate("https://youtu.be/zzzzzzzzzzz", "youtube", "zzzzzzzzzzz", "家庭 excluded")])
    result = discovery.scan(config(platforms=["youtube"], exclude_keywords=["excluded"]))
    assert len(result["candidates"]) == 1


def test_auto_profile_rejects_unconfirmed_rights_and_invalid_timezone():
    with pytest.raises(ValidationError):
        ReupProfileBody(topic="topic", automation="auto")
    with pytest.raises(ValidationError):
        ReupProfileBody(topic="topic", timezone="not-a-zone")


def test_api_owner_isolation_and_strict_profile_body(setup, tmp_path):
    store, owner, other = setup
    app = FastAPI()
    async def submit(*args):
        raise AssertionError("must not submit foreign jobs")
    runtime = install_reup_routes(app, store, submit, tmp_path)
    row = runtime.repository.enqueue(owner, candidate(), config())
    app.dependency_overrides[get_current_user_id] = lambda: other
    client = TestClient(app)
    assert client.post(f"/api/reup-agent/runs/{row['id']}/process").status_code == 404
    assert client.get("/api/reup-agent/runs").json() == []
    assert client.put("/api/reup-agent/profile", json={"topic":"topic","user_id":owner}).status_code == 422
    assert client.post(f"/api/jobs/{ReupRepository.localization_id(row)}/short-plan", json={}).status_code == 404


def test_real_metrics_preserve_missing_values_and_channel_identity(setup, tmp_path):
    store, owner, _ = setup
    async def submit(*args):
        raise AssertionError("not a localization call")
    runtime = ReupRuntime(store, submit, tmp_path)
    run = runtime.repository.enqueue(owner, candidate(), config(channel_id="owned"))
    runtime.repository.update(owner, run["id"], status="published", refs={"external_video_id":"remote"})
    class Metrics:
        def data_request(self, user_id, resource, params):
            assert params["id"] == "remote"
            return {"items":[{"snippet":{"channelId":"owned"},"statistics":{"viewCount":"123","likeCount":"7"}}]}
    runtime.metrics_client = Metrics()
    result = runtime.refresh_metrics(owner, run["id"])
    assert result["status"] == "published"
    assert result["refs"]["metrics"]["views"] == 123
    assert result["refs"]["metrics"]["comments"] is None


def test_upload_checkpoint_recovers_thumbnail_failure_without_second_video(setup, tmp_path, monkeypatch):
    store, owner, _ = setup
    async def submit(*args):
        raise AssertionError("A prepared run must not create a second job")
    runtime = ReupRuntime(store, submit, tmp_path)
    video = tmp_path / "ready.mp4"
    thumb = tmp_path / "thumb.png"
    video.write_bytes(b"video")
    thumb.write_bytes(b"image")
    run = runtime.repository.enqueue(owner, candidate(), config(channel_id="owned"))
    runtime.repository.update(owner, run["id"], status="ready", approved=True, refs={
        "video_path":str(video), "thumbnail_path":str(thumb), "title":"Title",
        "description":"Description", "tags":[],
    })
    monkeypatch.setattr(store, "get_social_account", lambda *args: {"account_ref":"owned"})

    class Publisher:
        begins = uploads = thumbnails = 0
        def begin(self, *args):
            self.begins += 1
            return "private-session"
        def upload(self, *args):
            self.uploads += 1
            return {"id":"remote-id"}
        def thumbnail(self, *args):
            self.thumbnails += 1
            if self.thumbnails == 1:
                raise RuntimeError("temporary error")
        def status(self, *args):
            return {"status":{"privacyStatus":"private", "uploadStatus":"processed"}}

    publisher = Publisher()
    runtime.publisher = publisher
    failed = asyncio.run(runtime.advance(owner, run["id"]))
    assert failed["status"] == "failed"
    assert "upload_session_uri" not in failed["refs"]
    assert failed["refs"]["external_video_id"] == "remote-id"
    runtime.repository.update(owner, run["id"], status="publishing")
    recovered = asyncio.run(runtime.advance(owner, run["id"]))
    assert recovered["status"] == "uploaded_private"
    assert (publisher.begins, publisher.uploads, publisher.thumbnails) == (1, 1, 2)


def test_approval_can_attest_rights_after_a_source_was_discovered(setup, tmp_path, monkeypatch):
    store, owner, _ = setup
    app = FastAPI()
    async def submit(*args):
        raise AssertionError("Approval must not create a localization job")
    runtime = install_reup_routes(app, store, submit, tmp_path)
    unconfirmed = ReupProfileBody(topic="family", rights_confirmed=False).model_dump()
    run = runtime.repository.enqueue(owner, candidate(), unconfirmed)
    runtime.repository.update(owner, run["id"], status="ready")
    app.dependency_overrides[get_current_user_id] = lambda: owner
    client = TestClient(app)
    assert client.post(f"/api/reup-agent/runs/{run['id']}/approve").status_code == 400
    runtime.repository.save_profile(owner, config())
    def publish(user_id, approved_run):
        assert approved_run["config"]["rights_confirmed"]
        runtime.repository.update(user_id, approved_run["id"], status="uploaded_private")
    monkeypatch.setattr(runtime, "_publish", publish)
    response = client.post(f"/api/reup-agent/runs/{run['id']}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "uploaded_private"


def test_package_preview_is_owner_scoped_and_thumbnail_stays_inside_pack(setup, tmp_path):
    store, owner, other = setup
    app = FastAPI()
    async def submit(*args):
        raise AssertionError("Preview must not submit work")
    runtime = install_reup_routes(app, store, submit, tmp_path)
    run = runtime.repository.enqueue(owner, candidate(), config())
    pack = tmp_path / "pack"
    pack.mkdir()
    thumb = pack / "thumbnail.png"
    thumb.write_bytes(b"png-data")
    (pack / "youtube_metadata.json").write_text(json.dumps({"recommended_title":"Title", "description":"Description",
                                                       "hashtags":["#test"], "tags":["test"]}), encoding="utf-8")
    runtime.repository.update(owner, run["id"], status="ready", refs={"pack_dir":str(pack), "thumbnail_path":str(thumb)})
    app.dependency_overrides[get_current_user_id] = lambda: other
    client = TestClient(app)
    assert client.get(f"/api/reup-agent/runs/{run['id']}/package").status_code == 404
    app.dependency_overrides[get_current_user_id] = lambda: owner
    preview = client.get(f"/api/reup-agent/runs/{run['id']}/package")
    assert preview.status_code == 200 and preview.json()["title"] == "Title"
    assert client.get(preview.json()["thumbnail_url"]).content == b"png-data"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"other-user-image")
    runtime.repository.update(owner, run["id"], refs={"pack_dir":str(pack), "thumbnail_path":str(outside)})
    assert client.get(f"/api/reup-agent/runs/{run['id']}/thumbnail").status_code == 404
