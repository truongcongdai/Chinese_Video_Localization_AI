# src/universal_video_ai/social/facebook.py
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

import requests

from .base import SocialUploader, SocialUploadResult

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"


class FacebookUploader(SocialUploader):
    platform_name = "Facebook"

    def is_configured(self) -> bool:
        return bool(os.environ.get("FACEBOOK_PAGE_ID") and os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN"))

    def upload(self, video_path: Path, title: str, description: str, hashtags: List[str],
               *, access_token: str = None, account_ref: str = None) -> SocialUploadResult:
        page_id = account_ref or os.environ.get("FACEBOOK_PAGE_ID")
        access_token = access_token or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
        if not page_id or not access_token:
            return self.not_configured_result(
                "Chưa kết nối Facebook Page. Vào mục Đăng lên mạng xã hội và bấm Kết nối, "
                "hoặc (chế độ 1 tài khoản chung) set FACEBOOK_PAGE_ID và FACEBOOK_PAGE_ACCESS_TOKEN "
                "trong .env. Xem README_WEB.md."
            )

        caption = f"{title}\n\n{description}\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        url = f"https://graph-video.facebook.com/{GRAPH_API_VERSION}/{page_id}/videos"

        try:
            with open(video_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"access_token": access_token, "description": caption},
                    files={"source": f},
                    timeout=600,
                )
            resp.raise_for_status()
            video_id = resp.json().get("id")
            return SocialUploadResult(
                platform=self.platform_name, success=True,
                message="Đã đăng lên Facebook Page.",
                remote_url=f"https://www.facebook.com/{video_id}" if video_id else None,
            )
        except Exception as exc:
            logger.exception("Facebook upload failed")
            return SocialUploadResult(platform=self.platform_name, success=False, message=str(exc))
