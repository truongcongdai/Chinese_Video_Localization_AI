"""CP8 owner-controlled YouTube publishing and scheduling."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from universal_video_ai.channel_agent.production_assets import ProductionAssetService
from universal_video_ai.channel_agent.social_publishing import (
    PublishingDestination,
    PublishingResult,
    SocialPublishingError,
    SocialPublishingService,
)
from universal_video_ai.channel_agent.youtube import GoogleOAuthTokenService, YouTubeReadOnlyService
from universal_video_ai.web.store import Store

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
PRIVACY_VALUES = {"private", "unlisted", "public"}


class ProductionPublishingError(SocialPublishingError): pass
class ProductionPublishingNotFound(ProductionPublishingError): pass


class YouTubeResumablePublisher:
    INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
    VIDEO_URL = "https://www.googleapis.com/youtube/v3/videos"
    THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

    def __init__(self, tokens: GoogleOAuthTokenService, *, http: Any = requests, timeout: int = 60) -> None:
        self.tokens, self.http, self.timeout = tokens, http, timeout

    def _token(self, user_id: int) -> str:
        try:
            return self.tokens.get_valid_access_token(user_id, required_scopes={YOUTUBE_UPLOAD_SCOPE})
        except Exception as exc:
            raise ProductionPublishingError(
                "YouTube authorization is unavailable or missing youtube.upload permission."
            ) from exc

    @staticmethod
    def _payload(response: Any) -> dict:
        try: value = response.json()
        except (TypeError, ValueError): value = {}
        return value if isinstance(value, dict) else {}

    def begin(self, user_id: int, job: dict) -> str:
        status: dict[str, Any] = {"privacyStatus": job["privacy"], "selfDeclaredMadeForKids": False}
        if job.get("schedule_utc"):
            status["privacyStatus"] = "private"
            status["publishAt"] = job["schedule_utc"]
        body = {"snippet": {"title": job["title"], "description": job["description"], "tags": job["tags"], "categoryId": "22", "defaultLanguage": "vi"}, "status": status}
        response = self.http.post(self.INIT_URL, params={"uploadType": "resumable", "part": "snippet,status"}, headers={"Authorization": f"Bearer {self._token(user_id)}", "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(job["upload_size"])}, json=body, timeout=self.timeout)
        if response.status_code not in {200, 201} or not response.headers.get("Location"):
            raise ProductionPublishingError(f"YouTube could not create a resumable upload session (HTTP {response.status_code}).")
        return str(response.headers["Location"])

    def upload(self, user_id: int, session_uri: str, video_path: str, size: int) -> dict:
        headers = {"Authorization": f"Bearer {self._token(user_id)}", "Content-Type": "video/mp4", "Content-Length": str(size), "Content-Range": f"bytes 0-{size - 1}/{size}"}
        last_error: Optional[Exception] = None
        for _ in range(3):
            try:
                with Path(video_path).open("rb") as stream:
                    response = self.http.put(session_uri, headers=headers, data=stream, timeout=self.timeout)
                if response.status_code in {200, 201}:
                    payload = self._payload(response)
                    if payload.get("id"): return payload
                if response.status_code == 308:
                    continue
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise ProductionPublishingError(f"YouTube upload failed (HTTP {response.status_code}).")
            except requests.RequestException as exc:
                last_error = exc
        raise ProductionPublishingError("YouTube resumable upload was interrupted; retry will resume the persisted job.") from last_error

    def status(self, user_id: int, video_id: str) -> dict:
        response = self.http.get(self.VIDEO_URL, params={"part": "status,processingDetails", "id": video_id}, headers={"Authorization": f"Bearer {self._token(user_id)}"}, timeout=self.timeout)
        payload = self._payload(response)
        items = payload.get("items") or []
        if response.status_code != 200 or not items:
            raise ProductionPublishingError("YouTube video status is unavailable.")
        return items[0]

    def thumbnail(self, user_id: int, video_id: str, path: str) -> None:
        with Path(path).open("rb") as stream:
            response = self.http.post(self.THUMBNAIL_URL, params={"videoId": video_id}, headers={"Authorization": f"Bearer {self._token(user_id)}", "Content-Type": "application/octet-stream"}, data=stream, timeout=self.timeout)
        if response.status_code not in {200, 201}: raise ProductionPublishingError("YouTube thumbnail upload failed.")


class ProductionPublishingService(SocialPublishingService):
    platform = "youtube"

    def __init__(self, store: Store, *, publisher: Optional[YouTubeResumablePublisher] = None, now: Any = None) -> None:
        super().__init__(store, now=now)
        self.tokens = GoogleOAuthTokenService(store)
        self.publisher = publisher or YouTubeResumablePublisher(self.tokens)

    def _item(self, user_id: int, item_id: int) -> dict:
        item = self.store.get_production_item(user_id, item_id)
        if not item: raise ProductionPublishingNotFound("Production item not found.")
        return item

    def _job(self, user_id: int, item_id: int, job_id: int, *, private: bool = False) -> dict:
        self._item(user_id, item_id)
        job = self.store.get_production_publishing_job(user_id, item_id, job_id, private=private)
        if not job or job.get("platform") != self.platform: raise ProductionPublishingNotFound("Publishing job not found.")
        return job

    def connection(self, user_id: int, *, verify: bool = False) -> dict:
        row = self.store.get_social_account(user_id, "youtube")
        scopes = set(str(row["scopes"] if row else "").replace(",", " ").split())
        base = self.tokens.connection_status(user_id).to_dict()
        base["upload_scope_granted"] = YOUTUBE_UPLOAD_SCOPE in scopes
        base["channel_id"] = row["account_ref"] if row else None
        if verify and base["upload_scope_granted"]:
            try:
                channel = YouTubeReadOnlyService(self.tokens).get_own_channel(user_id)
            except Exception as exc:
                raise ProductionPublishingError("Connected YouTube channel ownership could not be verified.") from exc
            base.update({"connection_verified": True, "channel_id": channel.channel_id, "account_name": channel.title})
        return base

    def _schedule(self, local_value: Optional[str], tz_name: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            return self.schedule(local_value, tz_name)
        except SocialPublishingError as exc:
            raise ProductionPublishingError(str(exc)) from exc

    def submit(self, user_id: int, item_id: int, *, render_job_id: int, metadata_asset_id: Optional[int] = None, thumbnail_path: Optional[str] = None, privacy: str = "private", schedule_local: Optional[str] = None, schedule_timezone: Optional[str] = None, dry_run: bool = True, confirm_publish: bool = False, confirm_public: bool = False) -> dict:
        item = self._item(user_id, item_id)
        if privacy not in PRIVACY_VALUES: raise ProductionPublishingError("Privacy must be private, unlisted, or public.")
        if not dry_run and not confirm_publish: raise ProductionPublishingError("An explicit publish action is required.")
        if privacy == "public" and not confirm_public: raise ProductionPublishingError("Public publishing requires explicit confirmation.")
        package = ProductionAssetService(self.store, provider=None).package(user_id, item_id)  # type: ignore[arg-type]
        if not package["asset_ready"]: raise ProductionPublishingError("Approved production asset package is not ready.")
        if privacy == "public" and (not package["rights_ready"] or package["rights_gate"] != "cleared"):
            raise ProductionPublishingError("Public publishing is blocked until the rights gate is cleared.")
        render = self.store.get_production_render_job(user_id, item_id, render_job_id)
        if not render or render.get("status") != "completed" or not render.get("qc", {}).get("passed"):
            raise ProductionPublishingError("A completed CP7B render with passing QC is required.")
        video = Path(str(render.get("output_path") or "")).resolve()
        if not video.is_file(): raise ProductionPublishingError("Rendered video file is unavailable.")
        metadata_rows = self.store.list_production_assets(user_id, item_id, asset_type="metadata_package", status="approved")
        metadata = next((row for row in metadata_rows if metadata_asset_id is None or row["id"] == metadata_asset_id), None)
        if not metadata: raise ProductionPublishingError("The requested approved metadata version was not found.")
        payload = metadata["payload"]
        title, description = str(payload.get("recommended_title") or "").strip(), str(payload.get("description") or "").strip()
        if not title or not description or len(title) > 100 or len(description) > 5000: raise ProductionPublishingError("Approved metadata title/description is invalid for YouTube.")
        tags = [str(tag).strip() for tag in (payload.get("tags") or []) if str(tag).strip()][:50]
        local, tz_name, utc = self._schedule(schedule_local, schedule_timezone)
        if utc and privacy != "private": raise ProductionPublishingError("YouTube scheduled publishing must use private privacy until publish time.")
        thumb_rows = self.store.list_production_assets(user_id, item_id, asset_type="thumbnail_brief", status="approved")
        thumb = thumb_rows[0] if thumb_rows else None
        thumb_file = Path(thumbnail_path).resolve() if thumbnail_path else None
        if thumb_file and not thumb_file.is_file(): raise ProductionPublishingError("Thumbnail file is unavailable.")
        connection = self.connection(user_id, verify=False)
        if not connection["upload_scope_granted"]: raise ProductionPublishingError("Connected YouTube account is missing youtube.upload permission.")
        if not connection.get("channel_id"): raise ProductionPublishingError("Reconnect YouTube so the owned channel identity can be verified.")
        channel_id, channel_title = connection.get("channel_id"), connection.get("account_name")
        canonical = {"user": user_id, "item": item_id, "render": render_job_id, "metadata": metadata["id"], "privacy": privacy, "schedule": utc, "channel": channel_id, "thumbnail": str(thumb_file) if thumb_file else None, "dry_run": dry_run}
        key = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        publish_package = {"production_item_id": item_id, "render_job_id": render_job_id, "video_path": str(video), "metadata_asset_id": metadata["id"], "metadata_version": metadata["version"], "title": title, "description": description, "tags": tags, "privacy": privacy, "schedule_local": local, "schedule_timezone": tz_name, "schedule_utc": utc, "target_channel": {"id": channel_id, "title": channel_title}, "thumbnail_path": str(thumb_file) if thumb_file else None, "thumbnail_asset_id": thumb["id"] if thumb else None, "rights_ready": package["rights_ready"], "rights_gate": package["rights_gate"]}
        job = self.store.create_production_publishing_job(user_id, item_id, {**publish_package, "platform": self.platform, "render_job_id": render_job_id, "metadata_asset_id": metadata["id"], "thumbnail_asset_id": thumb["id"] if thumb else None, "channel_id": channel_id, "channel_title": channel_title, "thumbnail_status": "pending" if thumb_file else "not_provided", "status": "draft" if dry_run else "queued", "dry_run": dry_run, "idempotency_key": key, "upload_size": video.stat().st_size, "package": publish_package})
        if not job: raise ProductionPublishingNotFound("Production item not found.")
        self.persist_destination(user_id, int(job["id"]), PublishingDestination(
            platform=self.platform, account_id=str(channel_id), account_name=channel_title,
            destination_type="channel_video", capabilities={
                "connected": True, "upload_supported": True, "schedule_supported": True,
                "analytics_supported": True,
            }, adapted_metadata={"title": title, "description": description, "tags": tags,
                                  "privacy": privacy, "schedule_utc": utc},
        ))
        self.persist_result(user_id, int(job["id"]), PublishingResult(
            platform=self.platform, status=str(job["status"])
        ))
        self.store.add_production_event(user_id, item_id, event_type="publishing_dry_run_validated" if dry_run else "publishing_queued", note=f"CP8 publishing job {job['id']}")
        return job

    def list(self, user_id: int, item_id: int) -> list[dict]:
        self._item(user_id, item_id)
        return [job for job in self.store.list_production_publishing_jobs(user_id, item_id)
                if job.get("platform") == self.platform]
    def get(self, user_id: int, item_id: int, job_id: int) -> dict: return self._job(user_id, item_id, job_id)

    def run(self, user_id: int, item_id: int, job_id: int) -> dict:
        job = self._job(user_id, item_id, job_id, private=True)
        if job["dry_run"]: return self._job(user_id, item_id, job_id)
        if job["status"] == "cancelled": raise ProductionPublishingError("Cancelled publishing jobs cannot run.")
        try:
            if job.get("external_video_id"): return self.refresh(user_id, item_id, job_id)
            session = job.get("upload_session_uri")
            if not session:
                session = self.publisher.begin(user_id, job)
                self.store.update_production_publishing_job(user_id, item_id, job_id, {"status": "uploading", "progress": 5, "upload_session_uri": session, "error": None})
            payload = self.publisher.upload(user_id, session, job["package"]["video_path"], int(job["upload_size"]))
            video_id = str(payload["id"])
            self.store.update_production_publishing_job(user_id, item_id, job_id, {"external_video_id": video_id, "external_url": f"https://www.youtube.com/watch?v={video_id}", "status": "processing", "progress": 90, "upload_offset": job["upload_size"]})
            self.persist_result(user_id, job_id, PublishingResult(
                platform=self.platform, status="processing", external_id=video_id,
                external_url=f"https://www.youtube.com/watch?v={video_id}", provider_state=payload,
            ))
            if job.get("thumbnail_path"):
                self.publisher.thumbnail(user_id, video_id, job["thumbnail_path"])
                self.store.update_production_publishing_job(user_id, item_id, job_id, {"thumbnail_status": "uploaded"})
            return self.refresh(user_id, item_id, job_id)
        except Exception as exc:
            self.store.update_production_publishing_job(user_id, item_id, job_id, {"status": "failed", "error": str(exc)})
            raise

    def refresh(self, user_id: int, item_id: int, job_id: int) -> dict:
        job = self._job(user_id, item_id, job_id, private=True)
        if not job.get("external_video_id"): raise ProductionPublishingError("Remote video has not been created.")
        remote = self.publisher.status(user_id, job["external_video_id"])
        status, processing = remote.get("status") or {}, remote.get("processingDetails") or {}
        upload = processing.get("processingStatus")
        if upload == "failed" or status.get("uploadStatus") in {"failed", "rejected", "deleted"}: local = "failed"
        elif job.get("schedule_utc") and self.now() < datetime.fromisoformat(job["schedule_utc"].replace("Z", "+00:00")): local = "scheduled"
        elif status.get("uploadStatus") in {"processed", "uploaded"} and upload in {None, "succeeded"}: local = "published"
        else: local = "processing"
        values: dict[str, Any] = {"status": local, "remote_status": remote, "progress": 100 if local in {"scheduled", "published"} else 95, "error": None}
        if local == "published": values["published_at"] = time.time()
        self.store.update_production_publishing_job(user_id, item_id, job_id, values)
        self.persist_result(user_id, job_id, PublishingResult(
            platform=self.platform, status=local, external_id=job.get("external_video_id"),
            external_url=job.get("external_url"), provider_state=remote,
        ))
        return self._job(user_id, item_id, job_id)

    def cancel(self, user_id: int, item_id: int, job_id: int) -> dict:
        job = self._job(user_id, item_id, job_id, private=True)
        if job["status"] not in {"draft", "queued", "failed"}: raise ProductionPublishingError("Only local draft, queued, or failed jobs can be cancelled; remote video was not deleted.")
        self.store.update_production_publishing_job(user_id, item_id, job_id, {"status": "cancelled", "error": "Local job cancelled; no remote delete was performed." if job.get("external_video_id") else None})
        self.persist_result(user_id, job_id, PublishingResult(
            platform=self.platform, status="cancelled", external_id=job.get("external_video_id"),
            external_url=job.get("external_url"),
            error="Local job cancelled; no remote delete was performed." if job.get("external_video_id") else None,
        ))
        return self._job(user_id, item_id, job_id)
