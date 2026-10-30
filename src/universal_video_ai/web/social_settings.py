"""Admin-managed OAuth app setup; ordinary users only see provider login pages."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from universal_video_ai.web.auth import require_admin
from universal_video_ai.web.oauth import get_oauth_client
from universal_video_ai.web.store import Store

__all__ = ["install_social_settings_routes"]
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSetup:
    label: str
    dashboard_url: str
    id_label: str = "Client ID"


_APPS = {
    "youtube": AppSetup("YouTube", "https://console.cloud.google.com/apis/credentials"),
    "facebook": AppSetup("Facebook Page", "https://developers.facebook.com/apps/", "App ID"),
    "tiktok": AppSetup("TikTok", "https://developers.tiktok.com/apps/", "Client key"),
}


class SocialAppBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: SecretStr | None = None


def install_social_settings_routes(app: FastAPI, store: Store) -> None:
    router = APIRouter(prefix="/api/admin/social-apps", tags=["Social setup"])

    def admin(request: Request) -> int:
        return require_admin(request, store)

    def summary(platform: str, request: Request) -> dict:
        spec = _APPS[platform]
        saved = store.get_social_app_credentials(platform)
        client = get_oauth_client(platform, saved)
        return {"platform": platform, "label": spec.label, "id_label": spec.id_label,
                "client_id": client.client_id, "secret_saved": bool(client.client_secret),
                "configured": client.is_configured(), "source": "ui" if saved else "environment",
                "dashboard_url": spec.dashboard_url,
                "redirect_uri": f"{str(request.base_url).rstrip('/')}/api/social/callback/{platform}"}

    @router.get("")
    def list_apps(request: Request, user_id: int = Depends(admin)) -> dict:
        return {"apps": [summary(platform, request) for platform in _APPS]}

    @router.put("/{platform}")
    def save_app(platform: str, body: SocialAppBody, request: Request, user_id: int = Depends(admin)) -> dict:
        if platform not in _APPS:
            raise HTTPException(404, "Nền tảng không hỗ trợ")
        client_id = body.client_id.strip()
        existing = get_oauth_client(platform, store.get_social_app_credentials(platform))
        secret = body.client_secret.get_secret_value().strip() if body.client_secret else existing.client_secret
        if not client_id or not secret or len(secret) > 4096:
            raise HTTPException(400, "Nhập mã ứng dụng và khóa bí mật do nền tảng cung cấp.")
        # A new client ID must not silently inherit the secret of a different app.
        if client_id != existing.client_id and not body.client_secret:
            raise HTTPException(400, "Khi đổi ứng dụng, cần nhập khóa bí mật tương ứng.")
        store.save_social_app_credentials(platform, client_id, secret, user_id)
        _logger.info("Social OAuth app configured platform=%s admin_id=%s", platform, user_id)
        return summary(platform, request)

    @router.delete("/{platform}")
    def delete_app(platform: str, request: Request, user_id: int = Depends(admin)) -> dict:
        if platform not in _APPS:
            raise HTTPException(404, "Nền tảng không hỗ trợ")
        store.delete_social_app_credentials(platform)
        return summary(platform, request)

    app.include_router(router)
