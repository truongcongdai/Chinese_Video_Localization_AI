# src/universal_video_ai/social/youtube.py
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List

import requests

from .base import SocialUploader, SocialUploadResult

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=multipart"


class YouTubeUploader(SocialUploader):
    platform_name = "YouTube"

    def is_configured(self) -> bool:
        return bool(
            os.environ.get("YOUTUBE_CLIENT_ID")
            and os.environ.get("YOUTUBE_CLIENT_SECRET")
            and os.environ.get("YOUTUBE_REFRESH_TOKEN")
        )

    def _get_access_token(self) -> str:
        resp = requests.post(TOKEN_URL, data={
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["access_token"]

    def upload(self, video_path: Path, title: str, description: str, hashtags: List[str],
               *, access_token: str = None, account_ref: str = None) -> SocialUploadResult:
        if not access_token and not self.is_configured():
            return self.not_configured_result(
                "Chưa kết nối tài khoản YouTube. Vào mục Đăng lên mạng xã hội và bấm Kết nối, "
                "hoặc (chế độ 1 tài khoản chung) set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, "
                "YOUTUBE_REFRESH_TOKEN trong .env. Xem README_WEB.md."
            )

        try:
            access_token = access_token or self._get_access_token()
            tags = [h.lstrip("#") for h in hashtags]
            metadata = {
                "snippet": {
                    "title": title[:100],
                    "description": description + ("\n\n" + " ".join(f"#{t}" for t in tags) if tags else ""),
                    "tags": tags,
                },
                "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
            }

            with open(video_path, "rb") as f:
                video_bytes = f.read()

            boundary = "vaiupload"
            body = (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata)}\r\n"
                f"--{boundary}\r\nContent-Type: video/mp4\r\n\r\n"
            ).encode("utf-8") + video_bytes + f"\r\n--{boundary}--".encode("utf-8")

            resp = requests.post(
                UPLOAD_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                data=body,
                timeout=1200,
            )
            resp.raise_for_status()
            video_id = resp.json().get("id")
            return SocialUploadResult(
                platform=self.platform_name, success=True,
                message="Đã tải lên YouTube (ở chế độ private — vào YouTube Studio để công khai).",
                remote_url=f"https://youtu.be/{video_id}" if video_id else None,
            )
        except Exception as exc:
            logger.exception("YouTube upload failed")
            return SocialUploadResult(platform=self.platform_name, success=False, message=str(exc))
