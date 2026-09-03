from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import importlib.util
import re
from typing import Any, Protocol

from .schemas import ResearchVideo


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,128}$")


def canonical_youtube_url(video_id: str) -> str:
    clean_id = str(video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(clean_id):
        raise ValueError("invalid YouTube video ID")
    return f"https://www.youtube.com/watch?v={clean_id}"


class YouTubeCollectorError(RuntimeError):
    """Base error for a truthful, unsuccessful metadata collection."""


class YouTubeCollectorUnavailableError(YouTubeCollectorError):
    """Raised when the configured collector cannot run in this installation."""


class YouTubeCollectorTimeoutError(YouTubeCollectorError):
    """Raised when the bounded collection deadline is exceeded."""


class YouTubeResearchCollector(Protocol):
    async def search(self, query: str, max_results: int) -> list[ResearchVideo]:
        """Return real normalized YouTube metadata without downloading media."""


class YtDlpYouTubeResearchCollector:
    """Bounded, metadata-only YouTube search backed by the yt-dlp library."""

    def __init__(
        self,
        *,
        hard_max_results: int,
        timeout_seconds: int,
        max_concurrency: int = 1,
    ) -> None:
        self.hard_max_results = max(1, int(hard_max_results))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("yt_dlp") is not None

    async def search(self, query: str, max_results: int) -> list[ResearchVideo]:
        clean_query = " ".join(str(query or "").split())
        if not clean_query:
            raise ValueError("query must not be empty")
        requested = int(max_results)
        if requested < 1 or requested > self.hard_max_results:
            raise ValueError(f"max_results must be between 1 and {self.hard_max_results}")
        if not self.is_available():
            raise YouTubeCollectorUnavailableError("yt-dlp is not installed")

        async with self._semaphore:
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_sync, clean_query, requested),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise YouTubeCollectorTimeoutError(
                    f"YouTube collection exceeded {self.timeout_seconds} seconds"
                ) from exc
            except YouTubeCollectorError:
                raise
            except Exception as exc:
                raise YouTubeCollectorError("YouTube metadata collection failed") from exc
        return self.normalize_search_result(raw, clean_query, requested)

    def _extract_sync(self, query: str, max_results: int) -> Mapping[str, Any]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise YouTubeCollectorUnavailableError("yt-dlp is not installed") from exc

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "writethumbnail": False,
            "noplaylist": False,
            "playlistend": max_results,
            "socket_timeout": self.timeout_seconds,
            "retries": 1,
            "fragment_retries": 0,
            "ignoreerrors": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        except Exception as exc:
            raise YouTubeCollectorError("YouTube extractor failed") from exc
        if not isinstance(result, Mapping):
            raise YouTubeCollectorError("YouTube extractor returned malformed metadata")
        return result

    @classmethod
    def normalize_search_result(
        cls,
        result: Mapping[str, Any],
        source_query: str,
        max_results: int,
        *,
        collected_at: datetime | None = None,
    ) -> list[ResearchVideo]:
        entries = result.get("entries")
        if entries is None:
            entries = [result]
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            raise YouTubeCollectorError("YouTube extractor returned malformed entries")

        observed_at = collected_at or datetime.now(timezone.utc)
        normalized: list[ResearchVideo] = []
        seen: set[str] = set()
        for raw in entries:
            if len(normalized) >= max(0, int(max_results)):
                break
            if not isinstance(raw, Mapping):
                continue
            video_id = str(raw.get("id") or "").strip()
            if not _VIDEO_ID_RE.fullmatch(video_id) or video_id in seen:
                continue
            seen.add(video_id)
            normalized.append(
                ResearchVideo(
                    video_id=video_id,
                    canonical_url=canonical_youtube_url(video_id),
                    title=str(raw.get("title") or "").strip(),
                    channel_id=str(raw.get("channel_id") or raw.get("uploader_id") or "").strip(),
                    channel_title=str(raw.get("channel") or raw.get("uploader") or "").strip(),
                    published_at=cls._published_at(raw),
                    view_count=cls._optional_nonnegative_int(raw.get("view_count")),
                    like_count=cls._optional_nonnegative_int(raw.get("like_count")),
                    comment_count=cls._optional_nonnegative_int(raw.get("comment_count")),
                    subscriber_count=cls._optional_nonnegative_int(
                        raw.get("channel_follower_count")
                    ),
                    description=str(raw.get("description") or "").strip(),
                    duration_seconds=cls._optional_nonnegative_int(raw.get("duration")),
                    thumbnail_url=str(raw.get("thumbnail") or "").strip(),
                    search_query=source_query,
                    collected_at=observed_at,
                )
            )
        if entries and not normalized:
            raise YouTubeCollectorError(
                "YouTube extractor returned no usable video metadata"
            )
        return normalized

    @staticmethod
    def _optional_nonnegative_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _published_at(raw: Mapping[str, Any]) -> datetime | None:
        timestamp = raw.get("timestamp") or raw.get("release_timestamp")
        if timestamp is not None:
            try:
                return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        upload_date = str(raw.get("upload_date") or "").strip()
        if len(upload_date) == 8 and upload_date.isdigit():
            try:
                return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None
