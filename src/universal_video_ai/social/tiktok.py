# src/universal_video_ai/social/tiktok.py
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

import requests

from .base import SocialUploader, SocialUploadResult

logger = logging.getLogger(__name__)

TIKTOK_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"


class TikTokUploader(SocialUploader):
    platform_name = "TikTok"

    def is_configured(self) -> bool:
        return bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))

    def upload(self, video_path: Path, title: str, description: str, hashtags: List[str],
               *, access_token: str = None, account_ref: str = None) -> SocialUploadResult:
        access_token = access_token or os.environ.get("TIKTOK_ACCESS_TOKEN")
        if not access_token:
            return self.not_configured_result(
                "Chưa kết nối tài khoản TikTok. Vào mục Đăng lên mạng xã hội và bấm Kết nối, "
                "hoặc (chế độ 1 tài khoản chung) set TIKTOK_ACCESS_TOKEN trong .env. Xem README_WEB.md."
            )

        caption = f"{title}\n{description}\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

        try:
            file_size = video_path.stat().st_size
            # TikTok's Content Posting API upload flow: (1) init the post
            # (returns an upload_url + publish_id), (2) PUT the raw video
            # bytes to that upload_url, (3) TikTok processes it async.
            # https://developers.tiktok.com/doc/content-posting-api-reference-upload-video/
            init_resp = requests.post(
                TIKTOK_INIT_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={
                    "post_info": {"title": caption, "privacy_level": "SELF_ONLY"},
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": file_size,
                        "chunk_size": file_size,
                        "total_chunk_count": 1,
                    },
                },
                timeout=30,
            )
            init_resp.raise_for_status()
            data = init_resp.json().get("data", {})
            upload_url = data.get("upload_url")
            publish_id = data.get("publish_id")
            if not upload_url:
                return SocialUploadResult(
                    platform=self.platform_name, success=False,
                    message=f"TikTok init thất bại: {init_resp.text[:300]}",
                )

            with open(video_path, "rb") as f:
                put_resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    },
                    data=f,
                    timeout=300,
                )
            if put_resp.status_code not in (200, 201):
                return SocialUploadResult(
                    platform=self.platform_name, success=False,
                    message=f"TikTok upload thất bại: HTTP {put_resp.status_code}",
                )

            return SocialUploadResult(
                platform=self.platform_name, success=True,
                message=f"Đã đăng lên TikTok (publish_id={publish_id}). Có thể mất vài phút để xử lý xong.",
            )
        except Exception as exc:
            logger.exception("TikTok upload failed")
            return SocialUploadResult(platform=self.platform_name, success=False, message=str(exc))
