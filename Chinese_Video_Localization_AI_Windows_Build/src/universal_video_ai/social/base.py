# src/universal_video_ai/social/base.py
"""
Common interface for "publish the finished video to a social platform".

IMPORTANT — read before wiring a real platform in:
Every platform below (TikTok, Facebook, YouTube) requires YOU to first
register a developer app on that platform and obtain OAuth credentials —
there is no way around this, it's how each platform authenticates that
uploads are coming from an approved application, and this is not something
that can be pre-configured for you generically. Roughly:

  - TikTok:   https://developers.tiktok.com/          -> Content Posting API
              needs a registered app + user OAuth token with
              `video.publish` scope. Sandbox apps can only post to your own
              test account until TikTok approves the app for production.
  - Facebook: https://developers.facebook.com/          -> Graph API
              (Page video upload) needs a Meta app + a Page access token
              with `pages_manage_posts` (and video permissions approved by
              Meta's App Review for anything beyond your own test users).
  - YouTube:  https://console.cloud.google.com/         -> YouTube Data API v3
              needs a Google Cloud project + OAuth client + a user-consented
              refresh token with the `youtube.upload` scope.

Until those env vars are set, each uploader below reports itself as
"not configured" rather than pretending to succeed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class SocialUploadResult:
    platform: str
    success: bool
    message: str
    remote_url: Optional[str] = None


class SocialUploader(ABC):
    platform_name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether the required credentials/env vars are present."""
        raise NotImplementedError

    @abstractmethod
    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        hashtags: List[str],
        *,
        access_token: Optional[str] = None,
        account_ref: Optional[str] = None,
    ) -> SocialUploadResult:
        """
        :param access_token: per-user token from a completed OAuth "connect"
            (see `web.oauth`/`web.store.social_accounts`). When given, this
            takes priority over the platform-wide env var credentials, so
            each logged-in user publishes under their own connected account
            rather than one shared server-wide account.
        :param account_ref: platform-specific id that goes with the token
            (e.g. the Facebook Page id the token was issued for).
        """
        raise NotImplementedError

    def not_configured_result(self, how_to_configure: str) -> SocialUploadResult:
        return SocialUploadResult(
            platform=self.platform_name,
            success=False,
            message=f"{self.platform_name} chưa được cấu hình. {how_to_configure}",
        )
