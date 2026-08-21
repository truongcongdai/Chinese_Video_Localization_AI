from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException, Request

from universal_video_ai.channel_agent.youtube import (
    GoogleOAuthTokenService,
    YouTubeAuthorizationError,
    YouTubePermissionError,
    YouTubeReadOnlyService,
)
from universal_video_ai.web.auth import create_session_cookie_value, get_current_user_id
from universal_video_ai.web.channel_agent_router import youtube_status
from universal_video_ai.web.oauth import GoogleOAuth
from universal_video_ai.web.store import Store


SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/yt-analytics.readonly"
)
START = date(2026, 7, 23)
END = date(2026, 8, 19)


class FakeStore:
    def __init__(self, rows: dict[int, dict[str, Any]] | None = None) -> None:
        self.rows = rows or {}
        self.lookups: list[tuple[int, str]] = []
        self.updates: list[tuple[Any, ...]] = []

    def get_social_account(self, user_id: int, platform: str) -> Any:
        self.lookups.append((user_id, platform))
        return self.rows.get(user_id)

    def update_social_access_token(
        self,
        user_id: int,
        platform: str,
        access_token: str,
        expires_at: float,
        scopes: str | None = None,
    ) -> None:
        self.updates.append((user_id, platform, access_token, expires_at, scopes))
        self.rows[user_id]["access_token"] = access_token
        self.rows[user_id]["expires_at"] = expires_at


def credential(**overrides: Any) -> dict[str, Any]:
    row = {
        "access_token": "user-access-token",
        "refresh_token": "user-refresh-token",
        "expires_at": 2_000_000_000.0,
        "scopes": SCOPES,
        "account_name": "Test Channel",
    }
    row.update(overrides)
    return row


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class FakeHTTP:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def service(store: FakeStore, responses: list[FakeResponse]) -> tuple[YouTubeReadOnlyService, FakeHTTP]:
    http = FakeHTTP(responses)
    return YouTubeReadOnlyService(GoogleOAuthTokenService(store), http=http), http


def analytics_payload(names: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columnHeaders": [{"name": name} for name in names], "rows": rows}


def test_connection_states_no_credentials_valid_and_missing_scope() -> None:
    store = FakeStore({2: credential(), 3: credential(scopes="https://www.googleapis.com/auth/youtube.upload")})
    tokens = GoogleOAuthTokenService(store)

    assert tokens.connection_status(1).credential_present is False
    assert tokens.connection_status(2).connected is True
    status = tokens.connection_status(3)
    assert status.connected is False
    assert status.reconnect_required is True
    with pytest.raises(YouTubePermissionError):
        tokens.get_valid_access_token(3)


def test_google_reconsent_keeps_upload_and_adds_readonly_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    url = GoogleOAuth().authorize_url("http://localhost/callback", "state-value")
    params = parse_qs(urlparse(url).query)

    assert params["prompt"] == ["consent"]
    assert params["access_type"] == ["offline"]
    assert set(params["scope"][0].split()) == {
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    }


def test_existing_social_account_store_persists_scope_evidence(tmp_path: Path) -> None:
    store = Store(tmp_path / "web.sqlite3")
    user_id = store.create_user("scope-user", "hash", credits=0, is_admin=False)
    store.upsert_social_account(
        user_id,
        "youtube",
        access_token="secret-access",
        refresh_token="secret-refresh",
        scopes=SCOPES,
    )

    row = store.get_social_account(user_id, "youtube")

    assert row is not None
    assert row["scopes"] == SCOPES
    public = GoogleOAuthTokenService(store).connection_status(user_id).to_dict()
    assert "secret-access" not in repr(public)
    assert "secret-refresh" not in repr(public)


def test_expired_token_is_refreshed_and_persisted() -> None:
    class OAuth:
        def refresh_access_token_details(self, refresh_token: str) -> dict[str, Any]:
            assert refresh_token == "user-refresh-token"
            return {"access_token": "fresh-token", "expires_in": 1800}

    store = FakeStore({7: credential(expires_at=100.0)})
    tokens = GoogleOAuthTokenService(store, oauth_factory=OAuth, now=lambda: 200.0)

    assert tokens.get_valid_access_token(7) == "fresh-token"
    assert store.updates[0][:3] == (7, "youtube", "fresh-token")


def test_failed_refresh_becomes_actionable_authorization_error() -> None:
    class OAuth:
        def refresh_access_token_details(self, refresh_token: str) -> dict[str, Any]:
            raise RuntimeError("provider detail must not escape")

    store = FakeStore({7: credential(expires_at=100.0)})
    tokens = GoogleOAuthTokenService(store, oauth_factory=OAuth, now=lambda: 200.0)

    with pytest.raises(YouTubeAuthorizationError, match="revoked"):
        tokens.get_valid_access_token(7)


def test_channel_identity_populated_and_uses_only_authenticated_user() -> None:
    store = FakeStore({42: credential()})
    api, http = service(store, [FakeResponse({"items": [{
        "id": "UC-own",
        "snippet": {
            "title": "My Channel", "description": "Own channel", "customUrl": "@mine",
            "thumbnails": {"high": {"url": "https://image.test/high.jpg"}},
        },
        "statistics": {"subscriberCount": "12", "viewCount": "345", "videoCount": "6"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU-own"}},
    }]})])

    channel = api.get_own_channel(42)

    assert channel.channel_id == "UC-own"
    assert channel.subscriber_count == 12
    assert channel.view_count == 345
    assert channel.video_count == 6
    assert channel.uploads_playlist_id == "UU-own"
    assert store.lookups == [(42, "youtube")]
    assert http.calls[0]["params"]["mine"] == "true"
    assert "user-access-token" not in channel.to_dict().values()


def test_channel_identity_supports_zero_and_missing_optional_metadata() -> None:
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse({"items": [{
        "id": "UC-new", "snippet": {"title": "New"},
        "statistics": {"subscriberCount": "0", "viewCount": "0", "videoCount": "0"},
    }]})])

    channel = api.get_own_channel(1)

    assert (channel.subscriber_count, channel.view_count, channel.video_count) == (0, 0, 0)
    assert channel.thumbnail_url is None
    assert channel.custom_url is None


def test_channel_identity_hides_subscriber_count_when_provider_hides_it() -> None:
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse({"items": [{
        "id": "UC-hidden", "snippet": {"title": "Hidden"},
        "statistics": {"hiddenSubscriberCount": True, "viewCount": "5", "videoCount": "1"},
    }]})])

    channel = api.get_own_channel(1)

    assert channel.hidden_subscriber_count is True
    assert channel.subscriber_count is None


def test_overview_normalizes_metrics() -> None:
    names = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
             "subscribersGained", "subscribersLost", "likes", "comments"]
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse(analytics_payload(names, [[100, 250.5, 90, 42.5, 4, 1, 8, 3]]))])

    report = api.get_overview(1, START, END)

    assert report.views == 100
    assert report.watch_time_minutes == 250.5
    assert report.average_view_duration_seconds == 90.0
    assert report.subscribers_lost == 1


def test_overview_empty_report_is_valid_zero_data() -> None:
    names = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
             "subscribersGained", "subscribersLost", "likes", "comments"]
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse(analytics_payload(names, []))])

    report = api.get_overview(1, START, END)

    assert report.views == 0
    assert report.watch_time_minutes == 0.0
    assert report.comments == 0


def test_overview_missing_fields_stay_null_and_invalid_values_fail() -> None:
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse(analytics_payload(["views"], [[10]]))])
    assert api.get_overview(1, START, END).likes is None

    api, _ = service(store, [FakeResponse(analytics_payload(["views"], [["not-a-number"]]))])
    with pytest.raises(Exception, match="invalid response"):
        api.get_overview(1, START, END)


def test_top_videos_preserve_order_and_batch_metadata_lookup() -> None:
    names = ["video", "views", "estimatedMinutesWatched", "averageViewDuration",
             "averageViewPercentage", "subscribersGained", "likes", "comments"]
    report = analytics_payload(names, [
        ["v2", 20, 30, 50, 40, 2, 3, 1],
        ["v1", 10, 12, 30, 25, 1, 1, 0],
    ])
    metadata = {"items": [
        {"id": "v1", "snippet": {"title": "First", "publishedAt": "2026-01-01T00:00:00Z"}},
        {"id": "v2", "snippet": {"title": "Second", "thumbnails": {"default": {"url": "thumb"}}}},
    ]}
    store = FakeStore({1: credential()})
    api, http = service(store, [FakeResponse(report), FakeResponse(metadata)])

    videos = api.get_top_videos(1, START, END)

    assert [video.video_id for video in videos] == ["v2", "v1"]
    assert [video.title for video in videos] == ["Second", "First"]
    assert len(http.calls) == 2
    assert http.calls[1]["params"]["id"] == "v2,v1"


def test_top_videos_empty_channel_skips_metadata_request() -> None:
    store = FakeStore({1: credential()})
    api, http = service(store, [FakeResponse(analytics_payload(["video", "views"], []))])

    assert api.get_top_videos(1, START, END) == []
    assert len(http.calls) == 1


def test_traffic_sources_unknown_category_and_zero_total() -> None:
    names = ["insightTrafficSourceType", "views", "estimatedMinutesWatched"]
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse(analytics_payload(names, [["NEW_SOURCE", 0, 0]]))])

    result = api.get_traffic_sources(1, START, END)

    assert result[0].source == "NEW_SOURCE"
    assert result[0].percentage_of_views == 0.0


def test_content_type_normalizes_categories_and_can_be_unavailable() -> None:
    names = ["creatorContentType", "views", "estimatedMinutesWatched"]
    store = FakeStore({1: credential()})
    api, _ = service(store, [FakeResponse(analytics_payload(names, [
        ["SHORTS", 75, 20], ["VIDEO_ON_DEMAND", 25, 40], ["FUTURE_TYPE", 0, 0],
    ]))])
    report = api.get_content_types(1, START, END)
    assert [item.content_type for item in report.items] == ["Shorts", "Long-form", "Other"]
    assert report.items[0].percentage_of_views == 75.0

    error = {"error": {"errors": [{"reason": "badRequest"}], "status": "INVALID_ARGUMENT"}}
    api, _ = service(store, [FakeResponse(error, status_code=400)])
    assert api.get_content_types(1, START, END).available is False


def test_disabled_feature_does_not_touch_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")
    store = FakeStore({1: credential()})

    with pytest.raises(Exception) as exc_info:
        youtube_status(verify=False, user_id=1, store=store)

    assert getattr(exc_info.value, "status_code", None) == 404
    assert store.lookups == []


def test_api_requires_session_and_resolves_only_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    monkeypatch.setenv("WEB_SESSION_SECRET", "test-only-session-secret")
    store = FakeStore({42: credential()})
    anonymous = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(anonymous)
    assert exc_info.value.status_code == 401

    cookie = create_session_cookie_value(42)
    authenticated = Request({
        "type": "http", "headers": [(b"cookie", f"vai_session={cookie}".encode("ascii"))],
    })
    resolved_user = get_current_user_id(authenticated)
    response = youtube_status(verify=False, user_id=resolved_user, store=store)

    assert response["connected"] is True
    assert store.lookups == [(42, "youtube")]
    assert "user-access-token" not in repr(response)
    assert "user-refresh-token" not in repr(response)
