# src/universal_video_ai/web/identity_oauth.py
"""
"Sign in with Google / GitHub / Facebook" for LOGGING IN / REGISTERING an
account on this app — as opposed to `oauth.py`, which is a *different*
OAuth flow for an *already logged-in* user connecting their own TikTok/
Facebook/YouTube account so this app can publish videos as them.

It's tempting to reuse `oauth.py`'s GoogleOAuth/FacebookOAuth classes for
this, but the two things need different scopes and return different data:
publish-oauth wants an upload-capable access token for a specific YouTube
channel/Facebook Page; identity-oauth just wants "who is this person"
(their stable provider id + email) so we can find-or-create their account
row, and no long-lived access token needs to be stored at all afterwards.
Keeping them separate avoids scope creep/confusion between "can post
videos as you" and "knows you signed in".

Reuses the SAME `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` and
`FACEBOOK_APP_ID`/`FACEBOOK_APP_SECRET` env vars as oauth.py (one Google
Cloud OAuth client / one Meta app can serve both purposes) — the admin
just needs to also add this flow's redirect URI
(`{your-domain}/api/identity/callback/{provider}`) to that same app's
"Authorized redirect URIs" list. GitHub is identity-login-only in this app
(nothing here publishes to GitHub), so it gets its own new env vars:
`GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`.
"""
from __future__ import annotations

import os
import secrets
import urllib.parse
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Optional, ParamSpec, TypeVar

import requests

__all__ = [
    "IdentityOAuth", "GoogleIdentityOAuth", "GitHubIdentityOAuth", "FacebookIdentityOAuth",
    "IdentityResult", "IdentityOAuthError", "get_identity_oauth_client", "new_state",
]


class IdentityOAuthError(ValueError):
    """A provider exchange failed without exposing request credentials."""


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _safe_identity_errors(operation: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(operation)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return operation(*args, **kwargs)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            # HTTPError URLs can contain Facebook's app secret and auth code;
            # JSON/parser errors can include provider response contents too.
            raise IdentityOAuthError(
                "Identity provider request failed; check connection and provider configuration."
            ) from None

    return wrapped


def new_state() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class IdentityResult:
    provider_user_id: str            # stable unique id from the provider (never changes, unlike email)
    email: Optional[str]
    display_name: Optional[str]
    # Callers must never treat an email alone as proof of account ownership.
    email_verified: bool = False


class IdentityOAuth:
    provider = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str) -> IdentityResult:
        raise NotImplementedError

    def not_configured_message(self) -> str:
        raise NotImplementedError


class GoogleIdentityOAuth(IdentityOAuth):
    provider = "google"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    SCOPE = "openid email profile"

    def is_configured(self) -> bool:
        return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))

    def not_configured_message(self) -> str:
        return (
            "Admin cần tạo OAuth Client (Web application) tại Google Cloud Console và set "
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET trong .env (dùng chung với tính năng kết nối "
            "YouTube nếu đã cấu hình) — nhớ thêm redirect URI của bước đăng nhập vào danh sách "
            "'Authorized redirect URIs' của OAuth Client đó."
        )

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE,
            "state": state,
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    @_safe_identity_errors
    def exchange_code(self, code: str, redirect_uri: str) -> IdentityResult:
        resp = requests.post(self.TOKEN_URL, data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=30)
        resp.raise_for_status()
        access_token = resp.json()["access_token"]

        info_resp = requests.get(
            self.USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15
        )
        info_resp.raise_for_status()
        info = info_resp.json()
        email = info.get("email")
        email_verified = info.get("email_verified") is True and isinstance(email, str) and bool(email.strip())
        return IdentityResult(
            provider_user_id=info["sub"],
            email=email.strip() if email_verified else None,
            display_name=info.get("name") or info.get("email"),
            email_verified=email_verified,
        )


class GitHubIdentityOAuth(IdentityOAuth):
    provider = "github"
    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    SCOPE = "read:user user:email"

    def is_configured(self) -> bool:
        return bool(os.environ.get("GITHUB_CLIENT_ID") and os.environ.get("GITHUB_CLIENT_SECRET"))

    def not_configured_message(self) -> str:
        return (
            "Admin cần tạo 'OAuth App' tại github.com/settings/developers, rồi set "
            "GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET trong .env."
        )

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": os.environ["GITHUB_CLIENT_ID"],
            "redirect_uri": redirect_uri,
            "scope": self.SCOPE,
            "state": state,
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    @_safe_identity_errors
    def exchange_code(self, code: str, redirect_uri: str) -> IdentityResult:
        resp = requests.post(
            self.TOKEN_URL,
            data={
                "client_id": os.environ["GITHUB_CLIENT_ID"],
                "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        access_token = resp.json()["access_token"]

        auth_header = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
        profile_resp = requests.get("https://api.github.com/user", headers=auth_header, timeout=15)
        profile_resp.raise_for_status()
        profile = profile_resp.json()

        # /user's public email has no verification claim. Always consult the
        # email endpoint, and never fall back to an unverified address.
        email = None
        try:
            emails_resp = requests.get("https://api.github.com/user/emails", headers=auth_header, timeout=15)
            emails_resp.raise_for_status()
            emails = emails_resp.json()
            verified = [
                entry for entry in emails
                if isinstance(entry, dict) and entry.get("verified") is True
                and isinstance(entry.get("email"), str) and entry["email"].strip()
            ]
            selected = next((entry for entry in verified if entry.get("primary") is True), None)
            if selected is None and verified:
                selected = verified[0]
            if selected is not None:
                email = selected["email"].strip()
        except (requests.RequestException, ValueError, TypeError):
            # Provider id still authenticates an existing identity when the
            # optional email endpoint is unavailable. No public-email fallback.
            pass

        return IdentityResult(
            provider_user_id=str(profile["id"]),
            email=email,
            display_name=profile.get("name") or profile.get("login"),
            email_verified=email is not None,
        )


class FacebookIdentityOAuth(IdentityOAuth):
    provider = "facebook"
    AUTH_URL = "https://www.facebook.com/v19.0/dialog/oauth"
    TOKEN_URL = "https://graph.facebook.com/v19.0/oauth/access_token"
    SCOPE = "public_profile,email"

    def is_configured(self) -> bool:
        return bool(os.environ.get("FACEBOOK_APP_ID") and os.environ.get("FACEBOOK_APP_SECRET"))

    def not_configured_message(self) -> str:
        return (
            "Admin cần tạo app tại developers.facebook.com và set FACEBOOK_APP_ID / "
            "FACEBOOK_APP_SECRET trong .env (dùng chung với tính năng kết nối Facebook nếu đã cấu "
            "hình) — nhớ thêm redirect URI của bước đăng nhập vào 'Valid OAuth Redirect URIs'."
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

    @_safe_identity_errors
    def exchange_code(self, code: str, redirect_uri: str) -> IdentityResult:
        resp = requests.get(self.TOKEN_URL, params={
            "client_id": os.environ["FACEBOOK_APP_ID"],
            "client_secret": os.environ["FACEBOOK_APP_SECRET"],
            "redirect_uri": redirect_uri,
            "code": code,
        }, timeout=30)
        resp.raise_for_status()
        access_token = resp.json()["access_token"]

        profile_resp = requests.get(
            "https://graph.facebook.com/me",
            params={"fields": "id,name,email", "access_token": access_token},
            timeout=15,
        )
        profile_resp.raise_for_status()
        profile = profile_resp.json()
        return IdentityResult(
            provider_user_id=profile["id"],
            email=profile.get("email"),
            display_name=profile.get("name"),
        )


_CLIENTS = {
    "google": GoogleIdentityOAuth,
    "github": GitHubIdentityOAuth,
    "facebook": FacebookIdentityOAuth,
}


def get_identity_oauth_client(provider: str) -> IdentityOAuth:
    key = provider.strip().lower()
    if key not in _CLIENTS:
        raise ValueError(f"Unknown identity provider: {provider!r}")
    return _CLIENTS[key]()
