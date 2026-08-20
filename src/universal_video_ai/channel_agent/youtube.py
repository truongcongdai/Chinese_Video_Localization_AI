"""Authenticated, read-only access to a user's own YouTube channel.

This module deliberately uses the application's existing per-user social
OAuth records and ``requests`` dependency.  It contains no upload or other
mutation endpoint.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Callable, Optional, Protocol

import requests

from universal_video_ai.web.oauth import GoogleOAuth

logger = logging.getLogger(__name__)

YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_ANALYTICS_READONLY_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
REQUIRED_CHANNEL_AGENT_SCOPES = frozenset(
    {YOUTUBE_READONLY_SCOPE, YOUTUBE_ANALYTICS_READONLY_SCOPE}
)

YOUTUBE_DATA_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"


class OAuthAccountStore(Protocol):
    def get_social_account(self, user_id: int, platform: str) -> Any: ...

    def update_social_access_token(
        self,
        user_id: int,
        platform: str,
        access_token: str,
        expires_at: Optional[float],
        scopes: Optional[str] = None,
    ) -> None: ...


class YouTubeChannelService(Protocol):
    def connection_status(self, user_id: int, *, verify: bool = False) -> "YouTubeConnectionStatus": ...

    def get_own_channel(self, user_id: int) -> "YouTubeChannelIdentity": ...


class YouTubeAnalyticsService(Protocol):
    def get_overview(
        self, user_id: int, start_date: date, end_date: date
    ) -> "YouTubeAnalyticsOverview": ...


class YouTubeReadOnlyError(RuntimeError):
    code = "youtube_error"
    status_code = 502


class YouTubeNotConnectedError(YouTubeReadOnlyError):
    code = "youtube_not_connected"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("YouTube account is not connected.")


class YouTubePermissionError(YouTubeReadOnlyError):
    code = "youtube_analytics_permission_missing"
    status_code = 403

    def __init__(self) -> None:
        super().__init__(
            "YouTube Analytics permission is missing. Reconnect YouTube to grant "
            "read-only analytics access."
        )


class YouTubeAuthorizationError(YouTubeReadOnlyError):
    code = "youtube_authorization_invalid"
    status_code = 401

    def __init__(self) -> None:
        super().__init__(
            "YouTube authorization has expired or was revoked. Please reconnect your account."
        )


class YouTubeQuotaError(YouTubeReadOnlyError):
    code = "youtube_quota_exceeded"
    status_code = 429

    def __init__(self) -> None:
        super().__init__("YouTube API quota is currently exhausted. Please try again later.")


class YouTubeUnavailableError(YouTubeReadOnlyError):
    code = "youtube_api_unavailable"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("YouTube is temporarily unavailable. Please try again later.")


class YouTubeAnalyticsUnavailableError(YouTubeReadOnlyError):
    code = "youtube_analytics_unavailable"
    status_code = 422

    def __init__(self) -> None:
        super().__init__("YouTube Analytics is not available for this report.")


class YouTubeChannelNotFoundError(YouTubeReadOnlyError):
    code = "youtube_channel_not_found"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("No YouTube channel was found for the connected account.")


class YouTubeProviderResponseError(YouTubeReadOnlyError):
    code = "youtube_invalid_response"

    def __init__(self) -> None:
        super().__init__("YouTube returned an invalid response. Please try again later.")


@dataclass(frozen=True)
class YouTubeConnectionStatus:
    credential_present: bool
    analytics_scope_granted: bool
    connected: bool
    connection_verified: Optional[bool] = None
    reconnect_required: bool = False
    account_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class YouTubeChannelIdentity:
    channel_id: str
    title: str
    description: Optional[str] = None
    custom_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    subscriber_count: Optional[int] = None
    hidden_subscriber_count: bool = False
    view_count: Optional[int] = None
    video_count: Optional[int] = None
    uploads_playlist_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class YouTubeAnalyticsOverview:
    start_date: date
    end_date: date
    views: Optional[int]
    watch_time_minutes: Optional[float]
    subscribers_gained: Optional[int]
    subscribers_lost: Optional[int]
    average_view_duration_seconds: Optional[float]
    average_view_percentage: Optional[float]
    likes: Optional[int]
    comments: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["start_date"] = self.start_date.isoformat()
        result["end_date"] = self.end_date.isoformat()
        return result


@dataclass(frozen=True)
class YouTubeTopVideo:
    video_id: str
    title: Optional[str]
    thumbnail_url: Optional[str]
    published_at: Optional[str]
    views: Optional[int]
    estimated_minutes_watched: Optional[float]
    average_view_duration_seconds: Optional[float]
    average_view_percentage: Optional[float]
    subscribers_gained: Optional[int]
    likes: Optional[int]
    comments: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class YouTubeTrafficSource:
    source: str
    views: Optional[int]
    watch_time_minutes: Optional[float]
    percentage_of_views: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class YouTubeContentTypeItem:
    content_type: str
    upstream_type: str
    views: Optional[int]
    watch_time_minutes: Optional[float]
    percentage_of_views: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class YouTubeContentTypeReport:
    available: bool
    items: tuple[YouTubeContentTypeItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "items": [item.to_dict() for item in self.items]}


def default_date_range(days: int = 28, *, today: Optional[date] = None) -> tuple[date, date]:
    if days < 1:
        raise ValueError("days must be positive")
    end_date = (today or date.today()) - timedelta(days=1)
    return end_date - timedelta(days=days - 1), end_date


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _parse_scopes(value: Optional[str]) -> frozenset[str]:
    return frozenset(str(value or "").replace(",", " ").split())


class GoogleOAuthTokenService:
    """Single place for per-user lookup, scope checks, and token refresh."""

    def __init__(
        self,
        store: OAuthAccountStore,
        *,
        oauth_factory: Callable[[], GoogleOAuth] = GoogleOAuth,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._oauth_factory = oauth_factory
        self._now = now

    def connection_status(self, user_id: int) -> YouTubeConnectionStatus:
        row = self._store.get_social_account(user_id, "youtube")
        if not row:
            return YouTubeConnectionStatus(False, False, False)
        scopes = _parse_scopes(_row_value(row, "scopes"))
        scope_ok = REQUIRED_CHANNEL_AGENT_SCOPES.issubset(scopes)
        has_access = bool(_row_value(row, "access_token"))
        has_refresh = bool(_row_value(row, "refresh_token"))
        expires_at = _row_value(row, "expires_at")
        locally_usable = has_refresh or (
            has_access and (expires_at is None or float(expires_at) > self._now() + 60)
        )
        return YouTubeConnectionStatus(
            credential_present=has_access or has_refresh,
            analytics_scope_granted=scope_ok,
            connected=scope_ok and locally_usable,
            reconnect_required=not scope_ok or not locally_usable,
            account_name=_row_value(row, "account_name"),
        )

    def get_valid_access_token(self, user_id: int) -> str:
        row = self._store.get_social_account(user_id, "youtube")
        if not row:
            raise YouTubeNotConnectedError()
        if not REQUIRED_CHANNEL_AGENT_SCOPES.issubset(_parse_scopes(_row_value(row, "scopes"))):
            raise YouTubePermissionError()

        access_token = _row_value(row, "access_token")
        expires_at = _row_value(row, "expires_at")
        needs_refresh = not access_token or (
            expires_at is not None and float(expires_at) <= self._now() + 60
        )
        if not needs_refresh:
            return str(access_token)

        refresh_token = _row_value(row, "refresh_token")
        if not refresh_token:
            raise YouTubeAuthorizationError()
        try:
            refreshed = self._oauth_factory().refresh_access_token_details(str(refresh_token))
        except Exception as exc:
            logger.warning("YouTube OAuth refresh failed for user_id=%s", user_id)
            raise YouTubeAuthorizationError() from exc
        token = refreshed.get("access_token")
        if not token:
            raise YouTubeAuthorizationError()
        new_expires_at = self._now() + float(refreshed.get("expires_in", 3600))
        self._store.update_social_access_token(
            user_id,
            "youtube",
            str(token),
            new_expires_at,
            refreshed.get("scope"),
        )
        return str(token)


class YouTubeReadOnlyService:
    """Normalized own-channel reports from official Google REST APIs."""

    def __init__(self, token_service: GoogleOAuthTokenService, *, http: Any = requests) -> None:
        self._tokens = token_service
        self._http = http

    def connection_status(self, user_id: int, *, verify: bool = False) -> YouTubeConnectionStatus:
        status = self._tokens.connection_status(user_id)
        if not verify or not status.connected:
            return status
        try:
            self.get_own_channel(user_id)
        except YouTubeReadOnlyError:
            return YouTubeConnectionStatus(
                credential_present=status.credential_present,
                analytics_scope_granted=status.analytics_scope_granted,
                connected=False,
                connection_verified=False,
                reconnect_required=True,
                account_name=status.account_name,
            )
        return YouTubeConnectionStatus(
            credential_present=status.credential_present,
            analytics_scope_granted=status.analytics_scope_granted,
            connected=True,
            connection_verified=True,
            reconnect_required=False,
            account_name=status.account_name,
        )

    def _get_json(self, user_id: int, url: str, params: dict[str, Any], *, analytics: bool = False) -> dict[str, Any]:
        token = self._tokens.get_valid_access_token(user_id)
        try:
            response = self._http.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("YouTube request transport failure for user_id=%s", user_id)
            raise YouTubeUnavailableError() from exc

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise YouTubeProviderResponseError() from exc
        if 200 <= response.status_code < 300:
            if not isinstance(payload, dict):
                raise YouTubeProviderResponseError()
            return payload

        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        reasons = {
            str(item.get("reason", ""))
            for item in error.get("errors", [])
            if isinstance(item, dict)
        }
        provider_status = str(error.get("status", ""))
        logger.warning(
            "YouTube provider request failed user_id=%s status=%s reasons=%s",
            user_id,
            response.status_code,
            sorted(reason for reason in reasons if reason),
        )
        if response.status_code == 401 or provider_status == "UNAUTHENTICATED":
            raise YouTubeAuthorizationError()
        if reasons.intersection({"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}):
            raise YouTubeQuotaError()
        if response.status_code == 403 or reasons.intersection(
            {"insufficientPermissions", "forbidden", "youtubeAnalyticsForbidden"}
        ):
            raise YouTubePermissionError()
        if analytics and response.status_code == 400:
            raise YouTubeAnalyticsUnavailableError()
        if response.status_code >= 500:
            raise YouTubeUnavailableError()
        raise YouTubeUnavailableError()

    def data_request(self, user_id: int, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        """Make an authenticated read-only YouTube Data API request.

        CP2 providers reuse the same token refresh and sanitized provider-error
        handling as CP1 instead of creating another Google client.
        """
        return self._get_json(user_id, f"{YOUTUBE_DATA_API}/{resource.lstrip('/')}", params)

    def get_own_channel(self, user_id: int) -> YouTubeChannelIdentity:
        payload = self._get_json(
            user_id,
            f"{YOUTUBE_DATA_API}/channels",
            {
                "part": "snippet,statistics,contentDetails",
                "mine": "true",
                "fields": (
                    "items(id,snippet(title,description,customUrl,thumbnails),"
                    "statistics(subscriberCount,hiddenSubscriberCount,viewCount,videoCount),"
                    "contentDetails/relatedPlaylists/uploads)"
                ),
            },
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise YouTubeProviderResponseError()
        if not items:
            raise YouTubeChannelNotFoundError()
        item = items[0]
        if not isinstance(item, dict) or not item.get("id"):
            raise YouTubeProviderResponseError()
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
        related = details.get("relatedPlaylists") if isinstance(details.get("relatedPlaylists"), dict) else {}
        thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        thumbnail_url = None
        for size in ("high", "medium", "default"):
            candidate = thumbnails.get(size)
            if isinstance(candidate, dict) and candidate.get("url"):
                thumbnail_url = str(candidate["url"])
                break
        hidden = bool(statistics.get("hiddenSubscriberCount", False))
        return YouTubeChannelIdentity(
            channel_id=str(item["id"]),
            title=str(snippet.get("title") or "YouTube channel"),
            description=snippet.get("description"),
            custom_url=snippet.get("customUrl"),
            thumbnail_url=thumbnail_url,
            subscriber_count=None if hidden else _optional_int(statistics.get("subscriberCount")),
            hidden_subscriber_count=hidden,
            view_count=_optional_int(statistics.get("viewCount")),
            video_count=_optional_int(statistics.get("videoCount")),
            uploads_playlist_id=related.get("uploads"),
        )

    def _analytics_report(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
        *,
        metrics: str,
        **params: Any,
    ) -> tuple[list[str], list[list[Any]]]:
        query = {
            "ids": "channel==MINE",
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "metrics": metrics,
            **params,
        }
        payload = self._get_json(user_id, YOUTUBE_ANALYTICS_API, query, analytics=True)
        headers_raw = payload.get("columnHeaders", [])
        rows = payload.get("rows", [])
        if not isinstance(headers_raw, list) or not isinstance(rows, list):
            raise YouTubeProviderResponseError()
        headers: list[str] = []
        for header in headers_raw:
            if not isinstance(header, dict) or not isinstance(header.get("name"), str):
                raise YouTubeProviderResponseError()
            headers.append(header["name"])
        if any(not isinstance(row, list) or len(row) > len(headers) for row in rows):
            raise YouTubeProviderResponseError()
        return headers, rows

    def get_overview(self, user_id: int, start_date: date, end_date: date) -> YouTubeAnalyticsOverview:
        metrics = (
            "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
            "subscribersGained,subscribersLost,likes,comments"
        )
        headers, rows = self._analytics_report(user_id, start_date, end_date, metrics=metrics)
        if not rows:
            values = {name: 0 for name in metrics.split(",")}
        else:
            values = _values_by_header(headers, rows[0])
        return YouTubeAnalyticsOverview(
            start_date=start_date,
            end_date=end_date,
            views=_optional_int(values.get("views")),
            watch_time_minutes=_optional_float(values.get("estimatedMinutesWatched")),
            subscribers_gained=_optional_int(values.get("subscribersGained")),
            subscribers_lost=_optional_int(values.get("subscribersLost")),
            average_view_duration_seconds=_optional_float(values.get("averageViewDuration")),
            average_view_percentage=_optional_float(values.get("averageViewPercentage")),
            likes=_optional_int(values.get("likes")),
            comments=_optional_int(values.get("comments")),
        )

    def get_top_videos(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
        *,
        limit: int = 10,
    ) -> list[YouTubeTopVideo]:
        metrics = (
            "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
            "subscribersGained,likes,comments"
        )
        headers, rows = self._analytics_report(
            user_id,
            start_date,
            end_date,
            metrics=metrics,
            dimensions="video",
            sort="-views",
            maxResults=min(25, max(1, limit)),
        )
        values = [_values_by_header(headers, row) for row in rows]
        video_ids = [str(item["video"]) for item in values if item.get("video")]
        metadata: dict[str, dict[str, Any]] = {}
        if video_ids:
            payload = self._get_json(
                user_id,
                f"{YOUTUBE_DATA_API}/videos",
                {
                    "part": "snippet",
                    "id": ",".join(video_ids),
                    "fields": "items(id,snippet(title,publishedAt,thumbnails))",
                },
            )
            items = payload.get("items")
            if not isinstance(items, list):
                raise YouTubeProviderResponseError()
            metadata = {
                str(item.get("id")): item.get("snippet", {})
                for item in items
                if isinstance(item, dict) and item.get("id") and isinstance(item.get("snippet", {}), dict)
            }
        result: list[YouTubeTopVideo] = []
        for item in values:
            video_id = str(item.get("video") or "")
            if not video_id:
                continue
            snippet = metadata.get(video_id, {})
            result.append(
                YouTubeTopVideo(
                    video_id=video_id,
                    title=snippet.get("title"),
                    thumbnail_url=_thumbnail_url(snippet.get("thumbnails")),
                    published_at=snippet.get("publishedAt"),
                    views=_optional_int(item.get("views")),
                    estimated_minutes_watched=_optional_float(item.get("estimatedMinutesWatched")),
                    average_view_duration_seconds=_optional_float(item.get("averageViewDuration")),
                    average_view_percentage=_optional_float(item.get("averageViewPercentage")),
                    subscribers_gained=_optional_int(item.get("subscribersGained")),
                    likes=_optional_int(item.get("likes")),
                    comments=_optional_int(item.get("comments")),
                )
            )
        return result

    def get_traffic_sources(
        self, user_id: int, start_date: date, end_date: date
    ) -> list[YouTubeTrafficSource]:
        headers, rows = self._analytics_report(
            user_id,
            start_date,
            end_date,
            metrics="views,estimatedMinutesWatched",
            dimensions="insightTrafficSourceType",
            sort="-views",
        )
        values = [_values_by_header(headers, row) for row in rows]
        total_views = sum(_optional_int(item.get("views")) or 0 for item in values)
        return [
            YouTubeTrafficSource(
                source=str(item.get("insightTrafficSourceType") or "UNKNOWN"),
                views=_optional_int(item.get("views")),
                watch_time_minutes=_optional_float(item.get("estimatedMinutesWatched")),
                percentage_of_views=(
                    ((_optional_int(item.get("views")) or 0) / total_views * 100.0)
                    if total_views else 0.0
                ),
            )
            for item in values
        ]

    def get_content_types(
        self, user_id: int, start_date: date, end_date: date
    ) -> YouTubeContentTypeReport:
        try:
            headers, rows = self._analytics_report(
                user_id,
                start_date,
                end_date,
                metrics="views,estimatedMinutesWatched",
                dimensions="creatorContentType",
                sort="-views",
            )
        except YouTubeAnalyticsUnavailableError:
            return YouTubeContentTypeReport(available=False)
        values = [_values_by_header(headers, row) for row in rows]
        total_views = sum(_optional_int(item.get("views")) or 0 for item in values)
        labels = {"SHORTS": "Shorts", "VIDEO_ON_DEMAND": "Long-form", "LIVE_STREAM": "Live"}
        items = tuple(
            YouTubeContentTypeItem(
                content_type=labels.get(str(item.get("creatorContentType")), "Other"),
                upstream_type=str(item.get("creatorContentType") or "UNSPECIFIED"),
                views=_optional_int(item.get("views")),
                watch_time_minutes=_optional_float(item.get("estimatedMinutesWatched")),
                percentage_of_views=(
                    ((_optional_int(item.get("views")) or 0) / total_views * 100.0)
                    if total_views else 0.0
                ),
            )
            for item in values
        )
        return YouTubeContentTypeReport(available=True, items=items)


def _values_by_header(headers: list[str], row: list[Any]) -> dict[str, Any]:
    return {name: row[index] if index < len(row) else None for index, name in enumerate(headers)}


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise YouTubeProviderResponseError() from exc


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise YouTubeProviderResponseError() from exc


def _thumbnail_url(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    for size in ("high", "medium", "default"):
        item = value.get(size)
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return None
