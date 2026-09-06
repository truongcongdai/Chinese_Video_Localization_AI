"""Shared CP10 publishing contracts used by YouTube and Facebook."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from universal_video_ai.web.store import Store


PUBLISHING_STATES = frozenset({
    "draft", "queued", "uploading", "processing", "scheduled",
    "published", "failed", "cancelled",
})


class SocialPublishingError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishingCapability:
    connected: bool
    upload_supported: bool
    schedule_supported: bool
    analytics_supported: bool = False
    page_video_supported: bool = False
    reels_supported: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


@dataclass(frozen=True)
class PublishingDestination:
    platform: str
    account_id: str
    account_name: Optional[str]
    destination_type: str
    capabilities: dict[str, Any]
    adapted_metadata: dict[str, Any]


@dataclass(frozen=True)
class PublishingResult:
    platform: str
    status: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    provider_state: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class SocialPublisher(Protocol):
    def capabilities(self, user_id: int) -> PublishingCapability: ...
    def upload(self, user_id: int, job: dict[str, Any]) -> dict[str, Any]: ...
    def status(self, user_id: int, external_id: str) -> dict[str, Any]: ...


class SocialPublishingService:
    """Common owner linkage, scheduling, destination, and result state."""

    platform = "base"

    def __init__(self, store: Store, *, now: Any = None) -> None:
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))

    def item(self, user_id: int, item_id: int) -> dict[str, Any]:
        item = self.store.get_production_item(user_id, item_id)
        if not item:
            raise SocialPublishingError("Production item not found.")
        return item

    def job(self, user_id: int, item_id: int, job_id: int,
            *, private: bool = False) -> dict[str, Any]:
        self.item(user_id, item_id)
        job = self.store.get_production_publishing_job(user_id, item_id, job_id, private=private)
        if not job or job.get("platform") != self.platform:
            raise SocialPublishingError("Publishing job not found.")
        return job

    def schedule(self, local_value: Optional[str], tz_name: Optional[str]
                 ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if not local_value:
            return None, None, None
        if not tz_name:
            raise SocialPublishingError("A timezone is required for scheduled publishing.")
        try:
            zone = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise SocialPublishingError("Unknown publishing timezone.") from exc
        try:
            local = datetime.fromisoformat(local_value)
        except ValueError as exc:
            raise SocialPublishingError("Schedule time must be ISO local date/time.") from exc
        if local.tzinfo is not None:
            raise SocialPublishingError(
                "Schedule local time must not include an offset; select its timezone separately."
            )
        utc = local.replace(tzinfo=zone).astimezone(timezone.utc)
        if utc <= self.now():
            raise SocialPublishingError("Scheduled publishing time must be in the future.")
        return local.isoformat(timespec="minutes"), tz_name, utc.isoformat().replace("+00:00", "Z")

    def persist_destination(self, user_id: int, job_id: int,
                            destination: PublishingDestination) -> dict[str, Any]:
        row = self.store.create_publishing_destination(
            user_id, job_id, asdict(destination)
        )
        if not row:
            raise SocialPublishingError("Publishing job not found.")
        return row

    def persist_result(self, user_id: int, job_id: int,
                       result: PublishingResult) -> dict[str, Any]:
        if result.status not in PUBLISHING_STATES:
            raise SocialPublishingError("Invalid publishing result state.")
        row = self.store.upsert_publishing_result(user_id, job_id, asdict(result))
        if not row:
            raise SocialPublishingError("Publishing job not found.")
        return row
