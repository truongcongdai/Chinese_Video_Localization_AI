# src/universal_video_ai/web/oauth.py
"""
"Connect your account" OAuth flows for TikTok / Facebook / YouTube.

This is what lets MULTIPLE people share one deployment of this web app,
each publishing under their own social account — as opposed to the old
model where a single TIKTOK_ACCESS_TOKEN / FACEBOOK_PAGE_ACCESS_TOKEN /
YOUTUBE_REFRESH_TOKEN env var meant only one person's account could ever
be used, no matter who was logged into the web UI.

The admin still has to register ONE developer app per platform (this is
unavoidable — it's how every platform verifies which application is
asking for access), then set that app's client id/secret as env vars
below. After that, every logged-in user clicks "Connect" and goes through
that platform's normal OAuth consent screen, and THEIR resulting token is
stored against THEIR account (see `store.upsert_social_account`) — the
admin never sees or handles individual users' credentials.

Where things stop being just code: TikTok's Content Posting API and
Facebook Page publishing both require the app to pass that platform's
App Review before it can publish for accounts outside your own developer
sandbox. Until reviewed, connections will work but publishing may be
limited to your own test accounts — that approval process, not this
code, is the bottleneck.
"""
from __future__ import annotations

import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import requests

__all__ = [
    "PlatformOAuth", "GoogleOAuth", "FacebookOAuth", "TikTokOAuth",
    "get_oauth_client", "new_state", "qr_code_url",
]


def new_state() -> str:
    return secrets.token_urlsafe(24)


def qr_code_url(target_url: str, size: int = 220) -> str:
    """Public QR-code rendering endpoint (no server-side QR library needed) —
    lets a user scan-to-open the OAuth consent link on their phone instead
    of clicking through on desktop, handy since people are usually already
    logged into TikTok/Facebook/YouTube on their phone, not their browser."""
    return (
        "https://api.qrserver.com/v1/create-qr-code/?size="
        f"{size}x{size}&data={urllib.parse.quote(target_url, safe='')}"
    )


@dataclass
class ConnectResult:
    access_token: Optional[str]
    refresh_token: Optional[str]
    expires_at: Optional[float]
    account_name: Optional[str]
    account_ref: Optional[str]


class PlatformOAuth:
    platform = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str) -> ConnectResult:
        raise NotImplementedError

    def not_configured_message(self) -> str:
        raise NotImplementedError


class GoogleOAuth(PlatformOAuth):
    platform = "youtube"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

    def is_configured(self) -> bool:
        return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))

    def not_configured_message(self) -> str:
        return (
            "Admin cần tạo OAuth Client (loại Web application) tại Google Cloud Console, "
            "bật 'YouTube Data API v3', rồi set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET trong .env. "
            "Xem README_WEB.md."
        )

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE,
            "access_type": "offline",   # request a refresh_token
            "prompt": "consent",        # force refresh_token on every connect, not just the first
            "state": state,
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> ConnectResult:
        resp = requests.post(self.TOKEN_URL, data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        access_token = data["access_token"]
        expires_at = time.time() + data.get("expires_in", 3600)

        account_name = None
        try:
            ch = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            ).json()
            items = ch.get("items") or []
            if items:
                account_name = items[0]["snippet"]["title"]
        except Exception:
            pass

        return ConnectResult(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            account_name=account_name,
            account_ref=None,
        )

    def refresh_access_token(self, refresh_token: str) -> str:
        resp = requests.post(self.TOKEN_URL, data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["access_token"]


class FacebookOAuth(PlatformOAuth):
    platform = "facebook"
    AUTH_URL = "https://www.facebook.com/v19.0/dialog/oauth"
    TOKEN_URL = "https://graph.facebook.com/v19.0/oauth/access_token"
    SCOPE = "pages_show_list,pages_manage_posts,pages_read_engagement"

    def is_configured(self) -> bool:
        return bool(os.environ.get("FACEBOOK_APP_ID") and os.environ.get("FACEBOOK_APP_SECRET"))

    def not_configured_message(self) -> str:
        return (
            "Admin cần tạo app tại developers.facebook.com (loại 'Business'), thêm sản phẩm "
            "'Facebook Login', rồi set FACEBOOK_APP_ID / FACEBOOK_APP_SECRET trong .env. Lưu ý: "
            "đăng video lên Page thật (ngoài tài khoản test) cần Meta App Review duyệt quyền "
            "pages_manage_posts trước — nếu bạn chưa có Meta Business, hãy tạo miễn phí tại "
            "business.facebook.com trước khi làm bước này. Xem README_WEB.md."
        )

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": os.environ["FACEBOOK_APP_ID"],
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": self.SCOPE,
            "response_type": "code",
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> ConnectResult:
        resp = requests.get(self.TOKEN_URL, params={
            "client_id": os.environ["FACEBOOK_APP_ID"],
            "client_secret": os.environ["FACEBOOK_APP_SECRET"],
            "redirect_uri": redirect_uri,
            "code": code,
        }, timeout=30)
        resp.raise_for_status()
        user_token = resp.json()["access_token"]

        # A user access token can't post to a Page directly — fetch the
        # user's pages and use the first one's own Page access token
        # (matches this app's "one connection == one place to publish"
        # model; a future version could let the user pick among several).
        pages_resp = requests.get(
            "https://graph.facebook.com/v19.0/me/accounts",
            params={"access_token": user_token},
            timeout=30,
        ).json()
        pages = pages_resp.get("data") or []
        if not pages:
            return ConnectResult(
                access_token=user_token, refresh_token=None, expires_at=None,
                account_name="(không tìm thấy Page nào — cần là quản trị viên của ít nhất 1 Facebook Page)",
                account_ref=None,
            )
        page = pages[0]
        return ConnectResult(
            access_token=page["access_token"],
            refresh_token=None,
            expires_at=None,  # Page tokens derived from a long-lived user token don't expire in practice
            account_name=page.get("name"),
            account_ref=page.get("id"),
        )


class TikTokOAuth(PlatformOAuth):
    platform = "tiktok"
    AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    SCOPE = "user.info.basic,video.publish"

    def is_configured(self) -> bool:
        return bool(os.environ.get("TIKTOK_CLIENT_KEY") and os.environ.get("TIKTOK_CLIENT_SECRET"))

    def not_configured_message(self) -> str:
        return (
            "Admin cần đăng ký app tại TikTok for Developers, bật sản phẩm 'Login Kit' + "
            "'Content Posting API', rồi set TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET trong .env. "
            "App chưa được TikTok audit chỉ đăng được vào draft/inbox riêng tư của tài khoản test. "
            "Xem README_WEB.md."
        )

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE,
            "state": state,
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> ConnectResult:
        resp = requests.post(self.TOKEN_URL, data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        access_token = data["access_token"]
        expires_at = time.time() + data.get("expires_in", 86400)

        account_name = None
        try:
            info = requests.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "display_name"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15,
            ).json()
            account_name = info.get("data", {}).get("user", {}).get("display_name")
        except Exception:
            pass

        return ConnectResult(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            account_name=account_name,
            account_ref=data.get("open_id"),
        )


_CLIENTS = {"youtube": GoogleOAuth, "facebook": FacebookOAuth, "tiktok": TikTokOAuth}


def get_oauth_client(platform: str) -> PlatformOAuth:
    key = platform.strip().lower()
    if key not in _CLIENTS:
        raise ValueError(f"Unknown platform: {platform!r}")
    return _CLIENTS[key]()
