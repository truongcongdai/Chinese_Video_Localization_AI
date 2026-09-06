"""CP10 Facebook Page publishing through the official Meta Graph API."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Optional

import requests

from universal_video_ai.channel_agent.production_assets import ProductionAssetService
from universal_video_ai.channel_agent.social_publishing import (
    PublishingCapability,
    PublishingDestination,
    PublishingResult,
    SocialPublishingError,
    SocialPublishingService,
)
from universal_video_ai.web.store import Store


class FacebookPublishingError(SocialPublishingError):
    pass


class FacebookPublishingNotFound(FacebookPublishingError):
    pass


def _safe_provider_state(value: Any) -> Any:
    """Remove credentials if a provider unexpectedly echoes them."""
    if isinstance(value, dict):
        return {key: _safe_provider_state(item) for key, item in value.items()
                if key.casefold() not in {"access_token", "token", "appsecret_proof"}}
    if isinstance(value, list):
        return [_safe_provider_state(item) for item in value]
    return value


class MetaGraphPublisher:
    """Narrow Graph API boundary; tokens never leave this server-side adapter."""

    def __init__(self, store: Store, *, http: Any = requests,
                 api_version: Optional[str] = None, timeout: int = 60) -> None:
        self.store, self.http, self.timeout = store, http, timeout
        self.api_version = api_version or os.environ.get("META_GRAPH_API_VERSION", "v19.0")
        self.graph = f"https://graph.facebook.com/{self.api_version}"

    def _account(self, user_id: int) -> Any:
        row = self.store.get_social_account(user_id, "facebook")
        if not row or not row["access_token"] or not row["account_ref"]:
            raise FacebookPublishingError("A connected owned Facebook Page is required.")
        return row

    def _headers(self, user_id: int) -> dict[str, str]:
        row = self._account(user_id)
        return {"Authorization": f"Bearer {row['access_token']}"}

    @staticmethod
    def _payload(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def capabilities(self, user_id: int, *, verify: bool = False) -> PublishingCapability:
        try:
            account = self._account(user_id)
        except FacebookPublishingError as exc:
            return PublishingCapability(False, False, False, reasons=(str(exc),))
        page_video, schedule = True, True
        reasons = ["Reels availability is not asserted until Meta exposes it for this Page/app."]
        if verify:
            response = self.http.get(
                f"{self.graph}/{account['account_ref']}",
                params={"fields": "id,name,tasks"}, headers=self._headers(user_id),
                timeout=self.timeout,
            )
            payload = self._payload(response)
            if response.status_code != 200 or str(payload.get("id") or "") != str(account["account_ref"]):
                return PublishingCapability(
                    True, False, False, reasons=("Connected Page ownership/capability verification failed.",)
                )
            tasks = set(payload.get("tasks") or [])
            if tasks and not tasks.intersection({"CREATE_CONTENT", "MANAGE"}):
                page_video = schedule = False
                reasons.append("The Page token does not expose a content creation task.")
        return PublishingCapability(
            connected=True, upload_supported=page_video,
            schedule_supported=schedule, page_video_supported=page_video,
            reels_supported=False, reasons=tuple(reasons),
        )

    def pages(self, user_id: int) -> list[dict[str, Any]]:
        account = self._account(user_id)
        user_token = account["refresh_token"]
        if not user_token:
            return [{"page_id": str(account["account_ref"]),
                     "page_name": account["account_name"], "selected": True}]
        response = self.http.get(
            f"{self.graph}/me/accounts", params={"fields": "id,name,tasks"},
            headers={"Authorization": f"Bearer {user_token}"}, timeout=self.timeout,
        )
        payload = self._payload(response)
        if response.status_code != 200:
            raise FacebookPublishingError("Managed Facebook Pages are unavailable.")
        return [{
            "page_id": str(page["id"]), "page_name": page.get("name"),
            "selected": str(page["id"]) == str(account["account_ref"]),
            "content_creation_supported": not page.get("tasks") or bool(
                set(page.get("tasks") or []).intersection({"CREATE_CONTENT", "MANAGE"})
            ),
        } for page in payload.get("data") or [] if isinstance(page, dict) and page.get("id")]

    def select_page(self, user_id: int, page_id: str) -> dict[str, Any]:
        account = self._account(user_id)
        user_token = account["refresh_token"]
        if not user_token:
            raise FacebookPublishingError("Reconnect Facebook before selecting another Page.")
        response = self.http.get(
            f"{self.graph}/me/accounts", params={"fields": "id,name,access_token,tasks"},
            headers={"Authorization": f"Bearer {user_token}"}, timeout=self.timeout,
        )
        payload = self._payload(response)
        selected = next((page for page in payload.get("data") or []
                         if isinstance(page, dict) and str(page.get("id")) == str(page_id)), None)
        if response.status_code != 200 or not selected or not selected.get("access_token"):
            raise FacebookPublishingError("The requested Page is not managed by this Facebook account.")
        tasks = set(selected.get("tasks") or [])
        if tasks and not tasks.intersection({"CREATE_CONTENT", "MANAGE"}):
            raise FacebookPublishingError("The requested Page does not permit content creation.")
        self.store.upsert_social_account(
            user_id, "facebook", str(selected["access_token"]), str(user_token),
            account["expires_at"], selected.get("name"), str(selected["id"]), account["scopes"],
        )
        return {"page_id": str(selected["id"]), "page_name": selected.get("name"),
                "selected": True}

    def upload(self, user_id: int, job: dict[str, Any]) -> dict[str, Any]:
        account = self._account(user_id)
        package = job["package"]
        if package.get("destination_type") != "page_video":
            raise FacebookPublishingError("Facebook Reels capability is unavailable for this connection.")
        video = Path(str(package["video_path"]))
        data: dict[str, Any] = {
            "title": job.get("title") or "",
            "description": package["adapted_metadata"]["caption"],
        }
        if job.get("schedule_utc"):
            data.update({
                "published": "false",
                "scheduled_publish_time": str(int(datetime.fromisoformat(
                    str(job["schedule_utc"]).replace("Z", "+00:00")
                ).timestamp())),
            })
        else:
            data["published"] = "true"
        with video.open("rb") as stream:
            response = self.http.post(
                f"{self.graph}/{account['account_ref']}/videos",
                headers=self._headers(user_id), data=data,
                files={"source": (video.name, stream, "video/mp4")}, timeout=self.timeout,
            )
        payload = self._payload(response)
        if response.status_code not in {200, 201} or not payload.get("id"):
            raise FacebookPublishingError(
                f"Meta Graph API rejected the Page video upload (HTTP {response.status_code})."
            )
        return _safe_provider_state(payload)

    def status(self, user_id: int, external_id: str) -> dict[str, Any]:
        response = self.http.get(
            f"{self.graph}/{external_id}",
            params={"fields": "id,permalink_url,published,status"},
            headers=self._headers(user_id), timeout=self.timeout,
        )
        payload = self._payload(response)
        if response.status_code != 200 or not payload.get("id"):
            raise FacebookPublishingError("Facebook remote video status is unavailable.")
        return _safe_provider_state(payload)


class FacebookPublishingService(SocialPublishingService):
    platform = "facebook"

    def __init__(self, store: Store, *, publisher: Optional[MetaGraphPublisher] = None,
                 now: Any = None) -> None:
        super().__init__(store, now=now)
        self.publisher = publisher or MetaGraphPublisher(store)

    def connection(self, user_id: int, *, verify: bool = False) -> dict[str, Any]:
        row = self.store.get_social_account(user_id, self.platform)
        capability = self.publisher.capabilities(user_id, verify=verify)
        return {
            **capability.to_dict(), "platform": self.platform,
            "page_id": row["account_ref"] if row else None,
            "page_name": row["account_name"] if row else None,
            "token_stored_server_side": bool(row and row["access_token"]),
        }

    def pages(self, user_id: int) -> list[dict[str, Any]]:
        return self.publisher.pages(user_id)

    def select_page(self, user_id: int, page_id: str) -> dict[str, Any]:
        if not str(page_id).strip():
            raise FacebookPublishingError("Facebook Page ID is required.")
        return self.publisher.select_page(user_id, str(page_id).strip())

    @staticmethod
    def _adapt_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("recommended_title") or "").strip()[:255]
        description = str(payload.get("description") or "").strip()
        raw_tags = payload.get("tags") or payload.get("hashtags") or []
        hashtags = []
        for value in raw_tags:
            normalized = "".join(str(value).strip().split())
            if normalized:
                hashtags.append("#" + normalized.lstrip("#"))
        hashtags = hashtags[:10]
        cta = str(payload.get("facebook_cta") or payload.get("call_to_action") or "").strip()
        parts = [part for part in (title, description, cta, " ".join(hashtags)) if part]
        caption = "\n\n".join(parts)[:63206]
        return {
            "title": title or None, "caption": caption,
            "short_cta": cta or None, "hashtags": hashtags,
            "source": "approved_metadata_package", "deterministic": True,
        }

    def submit(self, user_id: int, item_id: int, *, render_job_id: int,
               metadata_asset_id: Optional[int] = None,
               destination_type: str = "page_video",
               schedule_local: Optional[str] = None,
               schedule_timezone: Optional[str] = None,
               dry_run: bool = True, confirm_publish: bool = False) -> dict[str, Any]:
        self.item(user_id, item_id)
        if destination_type not in {"page_video", "reel"}:
            raise FacebookPublishingError("Facebook destination must be page_video or reel.")
        if not dry_run and not confirm_publish:
            raise FacebookPublishingError("An explicit Facebook publish action is required.")
        package = ProductionAssetService(self.store, provider=None).package(user_id, item_id)  # type: ignore[arg-type]
        if not package["asset_ready"]:
            raise FacebookPublishingError("Approved production asset package is not ready.")
        if not package["rights_ready"] or package["rights_gate"] != "cleared":
            raise FacebookPublishingError("Facebook publishing is blocked until the rights gate is cleared.")
        render = self.store.get_production_render_job(user_id, item_id, render_job_id)
        if not render or render.get("status") != "completed" or not render.get("qc", {}).get("passed"):
            raise FacebookPublishingError("A completed CP7B render with passing QC is required.")
        video = Path(str(render.get("output_path") or "")).resolve()
        if not video.is_file():
            raise FacebookPublishingError("Rendered video file is unavailable.")
        rows = self.store.list_production_assets(
            user_id, item_id, asset_type="metadata_package", status="approved"
        )
        metadata = next((row for row in rows if metadata_asset_id is None
                         or row["id"] == metadata_asset_id), None)
        if not metadata:
            raise FacebookPublishingError("The requested approved metadata version was not found.")
        adapted = self._adapt_metadata(metadata["payload"])
        if not adapted["caption"]:
            raise FacebookPublishingError("Approved metadata cannot produce a Facebook caption.")
        connection = self.connection(user_id)
        if not connection["connected"] or not connection.get("page_id"):
            raise FacebookPublishingError("A connected owned Facebook Page is required.")
        if destination_type == "reel" and not connection["reels_supported"]:
            raise FacebookPublishingError("Facebook Reels capability is unavailable for this Page/app.")
        if destination_type == "page_video" and not connection["page_video_supported"]:
            raise FacebookPublishingError("Facebook Page video publishing is unavailable.")
        try:
            local, timezone_name, utc = self.schedule(schedule_local, schedule_timezone)
        except SocialPublishingError as exc:
            raise FacebookPublishingError(str(exc)) from exc
        if utc and not connection["schedule_supported"]:
            raise FacebookPublishingError("Facebook scheduling is unavailable; no immediate publish was performed.")
        canonical = {
            "platform": self.platform, "user": user_id, "item": item_id,
            "render": render_job_id, "metadata": metadata["id"],
            "page": connection["page_id"], "destination": destination_type,
            "schedule": utc, "dry_run": dry_run,
        }
        key = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        publish_package = {
            "production_item_id": item_id, "render_job_id": render_job_id,
            "video_path": str(video), "metadata_asset_id": metadata["id"],
            "metadata_version": metadata["version"], "adapted_metadata": adapted,
            "destination_type": destination_type,
            "target_page": {"id": connection["page_id"], "name": connection["page_name"]},
            "schedule_local": local, "schedule_timezone": timezone_name, "schedule_utc": utc,
            "rights_ready": True, "rights_gate": "cleared",
            "capability": {key: connection[key] for key in (
                "page_video_supported", "reels_supported", "schedule_supported"
            )},
        }
        job = self.store.create_production_publishing_job(user_id, item_id, {
            "platform": self.platform, "render_job_id": render_job_id,
            "metadata_asset_id": metadata["id"], "channel_id": connection["page_id"],
            "channel_title": connection["page_name"], "title": adapted["title"] or "Facebook video",
            "description": adapted["caption"], "tags": adapted["hashtags"],
            "privacy": "page_default", "schedule_local": local,
            "schedule_timezone": timezone_name, "schedule_utc": utc,
            "status": "draft" if dry_run else "queued", "dry_run": dry_run,
            "idempotency_key": key, "upload_size": video.stat().st_size,
            "package": publish_package,
        })
        if not job:
            raise FacebookPublishingNotFound("Production item not found.")
        self.persist_destination(user_id, int(job["id"]), PublishingDestination(
            platform=self.platform, account_id=str(connection["page_id"]),
            account_name=connection["page_name"], destination_type=destination_type,
            capabilities=publish_package["capability"], adapted_metadata=adapted,
        ))
        self.persist_result(user_id, int(job["id"]), PublishingResult(
            platform=self.platform, status=str(job["status"])
        ))
        self.store.add_production_event(
            user_id, item_id,
            event_type="facebook_publishing_dry_run_validated" if dry_run else "facebook_publishing_queued",
            note=f"CP10 Facebook publishing job {job['id']}",
        )
        return job

    def list(self, user_id: int, item_id: int) -> list[dict[str, Any]]:
        self.item(user_id, item_id)
        return [job for job in self.store.list_production_publishing_jobs(user_id, item_id)
                if job.get("platform") == self.platform]

    def get(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        try:
            return self.job(user_id, item_id, job_id)
        except SocialPublishingError as exc:
            raise FacebookPublishingNotFound(str(exc)) from exc

    def run(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        job = self.job(user_id, item_id, job_id, private=True)
        if job["dry_run"]:
            return self.get(user_id, item_id, job_id)
        if job["status"] == "cancelled":
            raise FacebookPublishingError("Cancelled publishing jobs cannot run.")
        try:
            if job.get("external_video_id"):
                return self.refresh(user_id, item_id, job_id)
            self.store.update_production_publishing_job(
                user_id, item_id, job_id, {"status": "uploading", "progress": 5, "error": None}
            )
            remote = self.publisher.upload(user_id, job)
            external_id = str(remote["id"])
            external_url = str(remote.get("permalink_url") or f"https://www.facebook.com/{external_id}")
            self.store.update_production_publishing_job(user_id, item_id, job_id, {
                "status": "processing", "progress": 90, "external_video_id": external_id,
                "external_url": external_url, "upload_offset": job["upload_size"],
                "remote_status": remote,
            })
            self.persist_result(user_id, job_id, PublishingResult(
                platform=self.platform, status="processing", external_id=external_id,
                external_url=external_url, provider_state=remote,
            ))
            return self.refresh(user_id, item_id, job_id)
        except Exception as exc:
            message = str(exc)
            self.store.update_production_publishing_job(
                user_id, item_id, job_id, {"status": "failed", "error": message}
            )
            self.persist_result(user_id, job_id, PublishingResult(
                platform=self.platform, status="failed", error=message
            ))
            raise

    def refresh(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        job = self.job(user_id, item_id, job_id, private=True)
        external_id = job.get("external_video_id")
        if not external_id:
            raise FacebookPublishingError("Remote Facebook video has not been created.")
        remote = self.publisher.status(user_id, str(external_id))
        remote_status = remote.get("status") or {}
        video_status = str(remote_status.get("video_status") or remote_status.get("status") or "").casefold()
        if video_status in {"error", "failed", "deleted", "rejected"}:
            local = "failed"
        elif job.get("schedule_utc") and not bool(remote.get("published")):
            local = "scheduled"
        elif bool(remote.get("published")) or video_status in {"ready", "published", "complete"}:
            local = "published"
        else:
            local = "processing"
        external_url = remote.get("permalink_url") or job.get("external_url")
        values: dict[str, Any] = {
            "status": local, "remote_status": remote,
            "external_url": external_url, "progress": 100 if local in {"scheduled", "published"} else 95,
            "error": None,
        }
        if local == "published":
            values["published_at"] = time.time()
        self.store.update_production_publishing_job(user_id, item_id, job_id, values)
        self.persist_result(user_id, job_id, PublishingResult(
            platform=self.platform, status=local, external_id=str(external_id),
            external_url=external_url, provider_state=remote,
        ))
        return self.get(user_id, item_id, job_id)

    def cancel(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        job = self.job(user_id, item_id, job_id, private=True)
        if job["status"] not in {"draft", "queued", "failed"}:
            raise FacebookPublishingError(
                "Only local draft, queued, or failed jobs can be cancelled; no remote post was deleted."
            )
        message = "Local job cancelled; no remote delete was performed." if job.get("external_video_id") else None
        self.store.update_production_publishing_job(
            user_id, item_id, job_id, {"status": "cancelled", "error": message}
        )
        self.persist_result(user_id, job_id, PublishingResult(
            platform=self.platform, status="cancelled", external_id=job.get("external_video_id"),
            external_url=job.get("external_url"), error=message,
        ))
        return self.get(user_id, item_id, job_id)
