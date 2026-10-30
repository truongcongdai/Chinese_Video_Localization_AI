"""UI-managed OAuth credentials remain encrypted and restricted to admins."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from universal_video_ai.channel_agent.youtube import GoogleOAuthTokenService, REQUIRED_CHANNEL_AGENT_SCOPES
from universal_video_ai.web.auth import COOKIE_NAME, create_session_cookie_value
from universal_video_ai.web.oauth import get_oauth_client
from universal_video_ai.web.social_settings import install_social_settings_routes
from universal_video_ai.web.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SESSION_SECRET", "test-only-session-signing-secret")
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    store = Store(tmp_path / "social-apps.sqlite")
    admin = store.create_user("admin", "hash", is_admin=True)
    member = store.create_user("member", "hash")
    app = FastAPI()
    install_social_settings_routes(app, store)
    return store, admin, member, TestClient(app)


def login(client, user):
    client.cookies.set(COOKIE_NAME, create_session_cookie_value(user))


def test_only_admins_can_read_or_change_deployment_app_credentials(setup):
    store, admin, member, client = setup
    assert client.get("/api/admin/social-apps").status_code == 401
    login(client, member)
    assert client.get("/api/admin/social-apps").status_code == 403
    assert client.put("/api/admin/social-apps/youtube", json={"client_id": "app", "client_secret": "secret"}).status_code == 403
    assert store.get_social_app_credentials("youtube") == {}
    login(client, admin)
    assert client.get("/api/admin/social-apps").status_code == 200


def test_saved_app_is_used_without_env_changes_and_secret_is_never_returned(setup):
    store, admin, _, client = setup
    login(client, admin)
    response = client.put("/api/admin/social-apps/youtube", json={"client_id": "ui-client-id", "client_secret": "private-test-secret"})
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["redirect_uri"] == "http://testserver/api/social/callback/youtube"
    assert "private-test-secret" not in response.text
    assert "private-test-secret" not in client.get("/api/admin/social-apps").text
    with store._connect() as conn:
        encrypted = conn.execute("SELECT client_secret FROM social_oauth_apps").fetchone()[0]
    assert encrypted.startswith("enc:v1:") and "private-test-secret" not in encrypted
    oauth = get_oauth_client("youtube", store.get_social_app_credentials("youtube"))
    query = parse_qs(urlsplit(oauth.authorize_url("https://app.test/callback", "random-state")).query)
    assert query["client_id"] == ["ui-client-id"]


def test_same_app_can_keep_secret_but_a_new_app_requires_a_matching_secret(setup):
    store, admin, _, client = setup
    login(client, admin)
    client.put("/api/admin/social-apps/facebook", json={"client_id": "app-a", "client_secret": "secret-a"})
    assert client.put("/api/admin/social-apps/facebook", json={"client_id": "app-a"}).status_code == 200
    assert client.put("/api/admin/social-apps/facebook", json={"client_id": "app-b"}).status_code == 400
    assert store.get_social_app_credentials("facebook") == {"client_id": "app-a", "client_secret": "secret-a"}
    assert client.delete("/api/admin/social-apps/facebook").status_code == 200
    assert store.get_social_app_credentials("facebook") == {}


def test_youtube_refresh_uses_the_app_saved_in_ui(setup, monkeypatch):
    store, admin, member, _ = setup
    store.save_social_app_credentials("youtube", "ui-app", "ui-secret", admin)
    store.upsert_social_account(member, "youtube", access_token="expired", refresh_token="refresh", expires_at=1,
                                scopes=" ".join(REQUIRED_CHANNEL_AGENT_SCOPES))
    submitted = []
    def post(url, data, timeout):
        submitted.append(data)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"access_token": "fresh", "expires_in": 3600})
    monkeypatch.setattr("universal_video_ai.web.oauth.requests.post", post)
    assert GoogleOAuthTokenService(store).get_valid_access_token(member) == "fresh"
    assert submitted[0]["client_id"] == "ui-app" and submitted[0]["client_secret"] == "ui-secret"
    assert store.get_social_account(member, "youtube")["access_token"] == "fresh"
