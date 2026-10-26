from datetime import datetime, timezone

import pytest
import requests

from tests.test_cp8_publishing import setup_publish, submit, StubPublisher, Response, Tokens
from universal_video_ai.channel_agent.production_publishing import ProductionPublishingService, YouTubeResumablePublisher
from universal_video_ai.social.youtube import _MultipartVideoBody


def test_upload_queries_offset_and_sends_only_remaining_bytes(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"abcdefghij")
    calls = []

    class HTTP:
        def put(self, url, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return Response(308, headers={"Range": "bytes=0-3"})
            return Response(200, {"id": "done"})

    result = YouTubeResumablePublisher(Tokens(), http=HTTP()).upload(1, "session", str(path), 10)
    assert result["id"] == "done"
    assert calls[0]["headers"]["Content-Range"] == "bytes */10"
    assert calls[1]["headers"]["Content-Range"] == "bytes 4-9/10"
    assert calls[1]["data"] == b"efghij"


def test_legacy_upload_stream_has_content_length_without_buffering_video(tmp_path):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"x" * (3 * 1024 * 1024 + 3))
    body = _MultipartVideoBody(path, {"status":{"privacyStatus":"private"}}, "test-boundary")
    chunks = list(body)
    assert len(chunks) == 6
    assert max(map(len, chunks)) <= 1024 * 1024
    assert sum(map(len, chunks)) == len(body)
    import requests
    prepared = requests.Request("POST", "https://example.test", data=body).prepare()
    assert prepared.headers["Content-Length"] == str(len(body))
    assert "Transfer-Encoding" not in prepared.headers


def test_lost_final_response_recovers_id_without_sending_again(tmp_path, monkeypatch):
    monkeypatch.setattr("universal_video_ai.channel_agent.production_publishing.time.sleep", lambda _: None)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    ranges = []

    class HTTP:
        def put(self, url, **kwargs):
            ranges.append(kwargs["headers"]["Content-Range"])
            if len(ranges) == 1:
                return Response(308)
            if len(ranges) == 2:
                raise requests.Timeout("lost final response")
            return Response(200, {"id": "accepted"})

    assert YouTubeResumablePublisher(Tokens(), http=HTTP()).upload(1, "session", str(path), 5)["id"] == "accepted"
    assert ranges == ["bytes */5", "bytes 0-4/5", "bytes */5"]


def test_thumbnail_failure_retries_without_duplicate_video(tmp_path):
    store, owner, _, item, render, _ = setup_publish(tmp_path)
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"image")

    class FlakyPublisher(StubPublisher):
        def thumbnail(self, user_id, video_id, path):
            self.thumbnails += 1
            if self.thumbnails == 1:
                raise RuntimeError("temporary thumbnail failure")

    publisher = FlakyPublisher()
    service = ProductionPublishingService(store, publisher=publisher)
    job = submit(service, owner, item, render, thumbnail_path=str(thumb), dry_run=False, confirm_publish=True)
    with pytest.raises(RuntimeError):
        service.run(owner, item, job["id"])
    assert service.get(owner, item, job["id"])["thumbnail_status"] == "failed"
    assert service.run(owner, item, job["id"])["thumbnail_status"] == "uploaded"
    assert publisher.uploads == 1 and publisher.thumbnails == 2


@pytest.mark.parametrize("privacy,remote_time,expected", [
    ("private", None, "uploaded_private"),
    ("private", "2030-01-02T00:00:00Z", "scheduled"),
    ("private", "2020-01-01T00:00:00Z", "uploaded_private"),
    ("public", None, "published"),
])
def test_remote_privacy_and_schedule_are_authoritative(tmp_path, privacy, remote_time, expected):
    store, owner, _, item, render, _ = setup_publish(tmp_path)
    remote = {"status": {"privacyStatus": privacy, "uploadStatus": "processed"},
              "processingDetails": {"processingStatus": "succeeded"}}
    if remote_time:
        remote["status"]["publishAt"] = remote_time
    publisher = StubPublisher(remote)
    service = ProductionPublishingService(store, publisher=publisher, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    job = submit(service, owner, item, render, dry_run=False, confirm_publish=True)
    result = service.run(owner, item, job["id"])
    assert result["status"] == expected
    assert bool(result.get("published_at")) == (expected == "published")
