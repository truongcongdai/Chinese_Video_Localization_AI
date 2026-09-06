from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_cp7b_production_render import prepared_item
from universal_video_ai.channel_agent.production_publishing import (
    ProductionPublishingError, ProductionPublishingNotFound,
    ProductionPublishingService, YouTubeResumablePublisher, YOUTUBE_UPLOAD_SCOPE,
)
from universal_video_ai.web.store import Store
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.channel_agent_router import ProductionPublishingBody, router


class StubPublisher:
    def __init__(self, remote=None):
        self.begins = self.uploads = self.thumbnails = 0
        self.remote = remote or {"status": {"privacyStatus": "private", "uploadStatus": "uploaded"}, "processingDetails": {"processingStatus": "processing"}}
    def begin(self, user_id, job): self.begins += 1; return "https://upload.example/session-secret"
    def upload(self, user_id, session_uri, video_path, size):
        self.uploads += 1
        assert Path(video_path).is_file() and size > 0
        return {"id": "video-123"}
    def status(self, user_id, video_id): assert video_id == "video-123"; return self.remote
    def thumbnail(self, user_id, video_id, path): self.thumbnails += 1


def setup_publish(tmp_path):
    store, owner, foreign, item = prepared_item(tmp_path)
    video = tmp_path / "final.mp4"; video.write_bytes(b"owned final mp4")
    render = store.create_production_render_job(owner, item, request={}, approved_asset_refs={})
    store.update_production_render_job(owner, item, render["id"], {"status": "completed", "current_stage": "completed", "progress": 100, "output_path": str(video), "qc": {"passed": True}})
    expiry = datetime.now(timezone.utc).timestamp() + 3600
    scopes = YOUTUBE_UPLOAD_SCOPE + " https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"
    store.upsert_social_account(owner, "youtube", "token", "refresh", expiry, "Owned channel", "channel-1", scopes)
    return store, owner, foreign, item, render["id"], video


def submit(service, owner, item, render, **overrides):
    args = {"render_job_id": render, "privacy": "private", "dry_run": True}; args.update(overrides)
    return service.submit(owner, item, **args)


def test_dry_run_approved_package_and_session_redaction(tmp_path):
    store, owner, _, item, render, video = setup_publish(tmp_path)
    job = submit(ProductionPublishingService(store), owner, item, render)
    assert job["status"] == "draft" and job["dry_run"]
    assert job["package"]["video_path"] == str(video.resolve())
    assert job["package"]["metadata_version"] == 1 and job["package"]["title"] == "test"
    assert "upload_session_uri" not in job


def test_owner_isolation_and_idor(tmp_path):
    store, owner, foreign, item, render, _ = setup_publish(tmp_path)
    service = ProductionPublishingService(store); job = submit(service, owner, item, render)
    with pytest.raises(ProductionPublishingNotFound): service.get(foreign, item, job["id"])
    assert store.get_production_publishing_job(foreign, item, job["id"]) is None


def test_idempotency_prevents_duplicate_job(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path); service = ProductionPublishingService(store)
    assert submit(service, owner, item, render)["id"] == submit(service, owner, item, render)["id"]
    assert len(service.list(owner, item)) == 1


@pytest.mark.parametrize("privacy", ["private", "unlisted", "public"])
def test_privacy_values(tmp_path, privacy):
    store, owner, _, item, render, _ = setup_publish(tmp_path)
    if privacy == "public":
        with store._connect() as conn: conn.execute("UPDATE production_items SET rights_gate_status='cleared' WHERE id=?", (item,))
    assert submit(ProductionPublishingService(store), owner, item, render, privacy=privacy, confirm_public=privacy == "public")["privacy"] == privacy


def test_public_confirmation_and_rights_gate(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path); service = ProductionPublishingService(store)
    with pytest.raises(ProductionPublishingError, match="explicit confirmation"): submit(service, owner, item, render, privacy="public")
    with pytest.raises(ProductionPublishingError, match="rights gate"): submit(service, owner, item, render, privacy="public", confirm_public=True)


def test_schedule_timezone_and_past_rejection(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path)
    service = ProductionPublishingService(store, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    job = submit(service, owner, item, render, schedule_local="2026-01-02T07:00", schedule_timezone="Asia/Bangkok")
    assert job["schedule_utc"] == "2026-01-02T00:00:00Z"
    with pytest.raises(ProductionPublishingError, match="future"): submit(service, owner, item, render, schedule_local="2025-01-01T07:00", schedule_timezone="Asia/Bangkok")
    with pytest.raises(ProductionPublishingError, match="private"): submit(service, owner, item, render, privacy="unlisted", schedule_local="2026-01-03T07:00", schedule_timezone="Asia/Bangkok")


def test_explicit_publish_and_upload_scope(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path); service = ProductionPublishingService(store)
    with pytest.raises(ProductionPublishingError, match="explicit publish"): submit(service, owner, item, render, dry_run=False)
    expiry = datetime.now(timezone.utc).timestamp() + 3600
    store.upsert_social_account(owner, "youtube", "token", "refresh", expiry, "Owned", "channel-1", "https://www.googleapis.com/auth/youtube.readonly")
    with pytest.raises(ProductionPublishingError, match="youtube.upload"): submit(service, owner, item, render, dry_run=False, confirm_publish=True)


def test_resumable_upload_remote_id_sync_and_resume(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path); publisher = StubPublisher()
    service = ProductionPublishingService(store, publisher=publisher)
    job = submit(service, owner, item, render, dry_run=False, confirm_publish=True)
    result = service.run(owner, item, job["id"])
    assert result["external_video_id"] == "video-123" and result["status"] == "processing"
    assert publisher.begins == publisher.uploads == 1 and "upload_session_uri" not in result
    assert store.get_production_publishing_job(owner, item, job["id"], private=True)["upload_session_uri"].endswith("session-secret")


def test_remote_id_prevents_duplicate_upload(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path)
    remote = {"status": {"privacyStatus": "unlisted", "uploadStatus": "processed"}, "processingDetails": {"processingStatus": "succeeded"}}
    publisher = StubPublisher(remote); service = ProductionPublishingService(store, publisher=publisher)
    job = submit(service, owner, item, render, privacy="unlisted", dry_run=False, confirm_publish=True)
    assert service.run(owner, item, job["id"])["status"] == "published"
    assert service.run(owner, item, job["id"])["status"] == "published" and publisher.uploads == 1


def test_thumbnail_state_and_cancel_distinction(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path); thumbnail = tmp_path / "thumb.jpg"; thumbnail.write_bytes(b"image")
    publisher = StubPublisher(); service = ProductionPublishingService(store, publisher=publisher)
    job = submit(service, owner, item, render, thumbnail_path=str(thumbnail), dry_run=False, confirm_publish=True)
    assert service.run(owner, item, job["id"])["thumbnail_status"] == "uploaded" and publisher.thumbnails == 1
    with pytest.raises(ProductionPublishingError, match="remote video was not deleted"): service.cancel(owner, item, job["id"])
    queued = submit(service, owner, item, render, privacy="unlisted", dry_run=False, confirm_publish=True)
    assert service.cancel(owner, item, queued["id"])["status"] == "cancelled"


def test_repeated_migration(tmp_path):
    path = tmp_path / "repeat.sqlite3"; Store(path); Store(path)


def test_publishing_routes_are_authenticated_and_body_forbids_user_id():
    routes = [route for route in router.routes if "publishing" in route.path]
    assert routes
    for route in routes:
        assert any(dep.call is get_current_user_id for dep in route.dependant.dependencies)
    with pytest.raises(Exception):
        ProductionPublishingBody(render_job_id=1, user_id=999)


class Response:
    def __init__(self, code, payload=None, headers=None):
        self.status_code, self._payload, self.headers = code, payload or {}, headers or {}
    def json(self): return self._payload


class Tokens:
    def get_valid_access_token(self, user_id, required_scopes=None):
        assert required_scopes == {YOUTUBE_UPLOAD_SCOPE}
        return "secret-token"


class HTTP:
    def __init__(self): self.puts = 0
    def post(self, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
        return Response(200, headers={"Location": "https://upload.example/resumable"})
    def put(self, url, **kwargs):
        self.puts += 1
        return Response(503) if self.puts == 1 else Response(200, {"id": "remote-id"})


def test_official_resumable_provider_initializes_and_retries(tmp_path):
    video = tmp_path / "video.mp4"; video.write_bytes(b"video")
    http = HTTP(); provider = YouTubeResumablePublisher(Tokens(), http=http)
    job = {"privacy": "private", "title": "Approved", "description": "Approved description", "tags": ["vi"], "upload_size": video.stat().st_size, "schedule_utc": None}
    session = provider.begin(1, job)
    assert session.endswith("resumable")
    assert provider.upload(1, session, str(video), video.stat().st_size)["id"] == "remote-id"
    assert http.puts == 2
