# src/universal_video_ai/web/app.py
"""
Web UI/API replacing the Telegram bot as the primary way to use this
localization pipeline: paste a link, pick a language, watch progress,
preview + download the result, see history, and (once you've configured
your own platform credentials) publish straight to TikTok/Facebook/YouTube.

Run with: python scripts/run_web.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
import urllib.parse
import uuid
import time
import unicodedata
import inspect
import threading
import gc
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

import requests

from fastapi import FastAPI, Depends, HTTPException, Request, status, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from universal_video_ai.orchestrator.factory import create_localization_service
from universal_video_ai.orchestrator.service import (
    prepared_localization_to_dict, prepared_localization_from_dict,
)
from universal_video_ai.render.renderer import RenderConfig, AnimatedSubtitleConfig, VideoTemplateConfig
from universal_video_ai.render.animated_subtitles import SubtitleEffect, SubtitleStyle
from universal_video_ai.render import ocr_language_map
from universal_video_ai.render.quality_check import analyze_output_quality
from universal_video_ai.render.prepublish import inspect_for_publish, prepublish_report_to_dict
from universal_video_ai.render.video_review import review_finished_video, video_review_report_to_dict
from universal_video_ai.render.voice_director import direct_voice_cue
from universal_video_ai.render.visual_director import direct_visual_scene
from universal_video_ai.tts.tts import DEFAULT_VOICES_BY_LANGUAGE
from universal_video_ai.tts.tts import voice_for_language
from universal_video_ai.tts.voices import voices_for_language
from universal_video_ai.tts.backend import EdgeTTSBackend
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.timeline.service import _balanced_caption_chunks
from universal_video_ai.config import REDIS_URL, TEMP_DIR
from universal_video_ai.social import get_uploader
from universal_video_ai.downloader.youtube import YouTubeTools, YouTubeDownloadBody, YouTubeMetadataResponse

from .store import Store
from .auth import (
    COOKIE_NAME, hash_password, verify_password,
    create_session_cookie_value, get_current_user_id,
)
from . import oauth as oauth_module
from . import identity_oauth

logger = logging.getLogger("universal_video_ai.web")

app = FastAPI(title="Video Localization AI")

def _redact_redis_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.password:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        user = parsed.username or ""
        netloc = f"{user}:***@{host}{port}" if user else f"***@{host}{port}"
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return url

_CREATOR_AI_CACHE: Dict[tuple, tuple[float, Dict[str, Any]]] = {}
_CREATOR_AI_LOCK = threading.Lock()
_CREATOR_AI_CACHE_TTL_SECONDS = 3600
_CREATOR_AI_CACHE_MAX_SIZE = 100  # Maximum number of cached entries


def _evict_old_cache_entries():
    """Remove expired and oldest entries if cache exceeds max size."""
    now = time.time()
    
    # First, remove expired entries
    expired_keys = [
        key for key, (timestamp, _) in _CREATOR_AI_CACHE.items()
        if now - timestamp > _CREATOR_AI_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        del _CREATOR_AI_CACHE[key]
    
    # If still over limit, remove oldest entries (LRU)
    if len(_CREATOR_AI_CACHE) > _CREATOR_AI_CACHE_MAX_SIZE:
        # Sort by timestamp (oldest first)
        sorted_entries = sorted(
            _CREATOR_AI_CACHE.items(),
            key=lambda x: x[1][0]  # timestamp
        )
        # Remove oldest entries until under limit
        num_to_remove = len(_CREATOR_AI_CACHE) - _CREATOR_AI_CACHE_MAX_SIZE
        for key, _ in sorted_entries[:num_to_remove]:
            del _CREATOR_AI_CACHE[key]


_IMAGE_AI_PIPELINE: Any = None
_IMAGE_AI_DEVICE: Optional[str] = None
_IMAGE_AI_LOCK = threading.Lock()
_VIDEO_AI_PIPELINE: Any = None
_VIDEO_AI_MODEL: Optional[str] = None
_VIDEO_AI_LOCK = threading.Lock()

# Same default DB as the Telegram bot (scripts/run_bot.py's --db), but the
# web app gets its own table set (users/jobs/publish_log) inside it via
# Store's schema, so both can safely share one sqlite file if you want.
_DB_PATH = Path(os.environ.get("WEB_DB_PATH", TEMP_DIR / "database.sqlite3"))
_OUTPUT_BASE_DIR = TEMP_DIR / "output"
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Credits consumed per submitted job. Purely a usage-limiting knob (there's
# no billing/payment wired up) â€” an admin tops up a user's balance from the
# admin dashboard. Set to 0 to disable the whole credits gate.
JOB_COST_CREDITS = int(os.environ.get("JOB_COST_CREDITS", "1"))
WEB_RENDER_PRESET = os.environ.get("WEB_RENDER_PRESET", "fast")
WEB_RENDER_CRF = max(16, min(28, int(os.environ.get("WEB_RENDER_CRF", "20"))))
WEB_RENDER_TIMEOUT_SECONDS = int(os.environ.get("WEB_RENDER_TIMEOUT_SECONDS", "1800"))
TOP_UP_PACKAGES = {50: 50_000, 120: 100_000, 300: 250_000, 700: 500_000}

store = Store(_DB_PATH)


@app.on_event("startup")
def recover_jobs_interrupted_by_restart() -> None:
    """Background jobs cannot survive a process restart; expose that state."""
    recovered = store.fail_interrupted_jobs(JOB_COST_CREDITS)
    if recovered:
        logger.warning("Recovered %s interrupted jobs", recovered)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")

# Whether anyone can self-register a new account (via email/phone/Google/
# GitHub/Facebook) at any time, vs. the original "only the very first user,
# ever" model. The very first account created (by whatever method) is
# always made admin regardless of this setting. Default true: this app is
# meant to support multiple people signing up on their own now.
OPEN_REGISTRATION = os.environ.get("OPEN_REGISTRATION", "true").lower() not in ("0", "false", "no")

# SaaS/multi-user mode is the safe default: publishing must use the social
# account connected by the current user.  The legacy shared tokens can only be
# used when an operator explicitly opts in (useful for a private, single-user
# installation, but unsafe as an implicit fallback on a client-facing server).
ALLOW_SHARED_SOCIAL_CREDENTIALS = os.environ.get(
    "ALLOW_SHARED_SOCIAL_CREDENTIALS", "false"
).lower() in ("1", "true", "yes")

# Credits granted to BOTH the new user and whoever invited them, when
# registering with a valid ?ref= referral code. Set to 0 to disable bonuses
# while keeping the referral tracking itself.
REFERRAL_BONUS_CREDITS = int(os.environ.get("REFERRAL_BONUS_CREDITS", "20"))

_LOGO_UPLOAD_DIR = TEMP_DIR / "web_uploads" / "logos"
_LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_PRODUCT_MEDIA_UPLOAD_DIR = _REPO_ROOT / "local_data" / "web_uploads" / "product_media"
_PRODUCT_MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Human-readable Vietnamese labels for language codes, shown in the
# frontend's dropdowns. Target-language options come straight from
# whatever TTS actually has a voice for (DEFAULT_VOICES_BY_LANGUAGE) so the
# list never silently drifts out of sync with what actually works.
LANGUAGE_LABELS = {
    "vi": "Tiáº¿ng Viá»‡t", "en": "Tiáº¿ng Anh", "zh": "Tiáº¿ng Trung (giáº£n thá»ƒ)",
    "zh-tw": "Tiáº¿ng Trung (phá»“n thá»ƒ)", "ja": "Tiáº¿ng Nháº­t", "ko": "Tiáº¿ng HÃ n",
    "fr": "Tiáº¿ng PhÃ¡p", "de": "Tiáº¿ng Äá»©c", "es": "Tiáº¿ng TÃ¢y Ban Nha",
    "pt": "Tiáº¿ng Bá»“ ÄÃ o Nha", "ru": "Tiáº¿ng Nga", "th": "Tiáº¿ng ThÃ¡i",
    "id": "Tiáº¿ng Indonesia", "ar": "Tiáº¿ng áº¢ Ráº­p", "hi": "Tiáº¿ng Hindi",
}

# In-memory guard against double-submitting the same job id concurrently;
# actual job state lives in the DB (store) so it survives restarts for
# already-finished jobs, just not for one that was mid-run at restart time.
_running_tasks: dict[str, asyncio.Task] = {}


def require_admin_user_id(user_id: int = Depends(get_current_user_id)) -> int:
    user = store.get_user_by_id(user_id)
    if not user or not user["is_admin"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "YÃªu cáº§u quyá»n admin")
    return user_id


# ---------------------------------------------------------------- schemas --

class LoginBody(BaseModel):
    identifier: str  # username, email, or phone number â€” whichever the account was created with
    password: str


class RegisterBody(BaseModel):
    username: str
    contact_identifier: str  # email address or phone number
    password: str
    referral_code: Optional[str] = None  # whoever invited this person, if anyone


class NewJobBody(BaseModel):
    url: str
    target_language: str = "vi"
    # "auto" = auto-detect the spoken language (and pick a matching OCR
    # pack for on-screen text) instead of assuming Chinese.
    source_language: str = "auto"
    # Optional brand logo overlay for this job, set via
    # POST /api/upload-logo beforehand (logo_path is the id it returns).
    logo_path: Optional[str] = None
    logo_corner: str = "bottom_right"  # top_left | top_right | bottom_left | bottom_right
    logo_size_px: int = 120
    # Explicit Edge-TTS voice id from GET /api/voices, e.g.
    # "vi-VN-NamMinhNeural" â€” None uses the target language's default voice.
    tts_voice: Optional[str] = None
    # When True, the job stops right after translation (status="review")
    # instead of rendering straight through, so the person can edit the
    # translated text first via PUT .../segments then POST .../render.
    review_before_render: bool = False
    # Animated subtitle configuration
    animated_subtitle_config: Optional[Dict[str, Any]] = None
    # Queue management
    priority: str = "normal"  # normal | high
    max_concurrent: int = 2
    # Video template configuration
    video_template_config: Optional[Dict[str, Any]] = None


class CreatorJobBody(BaseModel):
    topic: str
    script: Optional[str] = None
    narration_script: Optional[str] = None
    target_language: str = "vi"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30
    transition: str = "fade"
    tts_voice: Optional[str] = None
    image_provider: str = "stock"  # stock | cpu_ai | ai_video
    product_media_paths: List[str] = Field(default_factory=list)


class CreatorSuggestionBody(BaseModel):
    topic: str
    target_language: str = "vi"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30
    transition: str = "fade"
    advanced_options: Optional[Dict[str, Any]] = None
    provider: str = "gemini"  # gemini | openai | ollama | openrouter


class AffiliateReviewBody(BaseModel):
    product_url: Optional[str] = None
    product_name: str
    product_claims: str = ""
    pros: str = ""
    cons: str = ""
    audience: str = ""
    real_experience: str
    model_prompt: str = ""
    target_language: str = "vi"
    duration_seconds: int = 30
    platform: str = "tiktok_shop"  # tiktok_shop | reels | shorts
    creative_format: str = "ugc_problem_solution"  # ugc_problem_solution | demo_proof | before_after
    provider: str = "auto"


class BulkDeleteBody(BaseModel):
    job_ids: List[str]


class SegmentBody(BaseModel):
    start: float
    end: float
    text: str


class UpdateSegmentsBody(BaseModel):
    segments: List[SegmentBody]


class FeedbackBody(BaseModel):
    message: str
    page: Optional[str] = None


class TopUpRequestBody(BaseModel):
    credits: int
    amount_vnd: int
    payment_method: str = "bank_transfer"
    note: Optional[str] = None


class TTSSynthesizeBody(BaseModel):
    text: str
    language: str = "vi"
    voice: Optional[str] = None
    rate: str = "+0%"
    pitch: str = "+0Hz"


class VideoPresetBody(BaseModel):
    name: str
    template: str
    transition: str
    color_effect: str
    audio_filters: Optional[Dict[str, Any]] = None
    video_quality: Optional[str] = None
    is_default: bool = False


class TopUpDecisionBody(BaseModel):
    admin_note: Optional[str] = None


def _env_first(*names: str) -> str:
    """Read the first non-empty setting, tolerating whitespace in .env."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _payment_config(amount_vnd: int = 0, transfer_content: str = "") -> Dict[str, Any]:
    bank_id = _env_first("PAYMENT_BANK_ID", "BANK_ID", "BANK_BIN")
    account_number = _env_first("PAYMENT_ACCOUNT_NUMBER", "BANK_ACCOUNT_NUMBER")
    account_name = _env_first("PAYMENT_ACCOUNT_NAME", "BANK_ACCOUNT_NAME")
    bank_name = _env_first("PAYMENT_BANK_NAME", "BANK_NAME") or bank_id
    explicit_qr = _env_first("PAYMENT_QR_URL")
    qr_url = explicit_qr
    if not qr_url and bank_id and account_number:
        query = urllib.parse.urlencode({
            "amount": max(0, amount_vnd),
            "addInfo": transfer_content,
            "accountName": account_name,
        })
        qr_url = (
            f"https://img.vietqr.io/image/{urllib.parse.quote(bank_id, safe='')}-"
            f"{urllib.parse.quote(account_number, safe='')}-compact2.png?{query}"
        )
    return {
        "configured": bool(qr_url and account_number),
        "bank_id": bank_id, "bank_name": bank_name,
        "account_number": account_number, "account_name": account_name,
        "qr_url": qr_url,
    }


class PublishBody(BaseModel):
    platforms: List[str]
    title: str
    description: str = ""
    hashtags: List[str] = []


class SchedulePublishBody(PublishBody):
    scheduled_at: float


class CreditsAdjustBody(BaseModel):
    delta: Optional[int] = None
    set_to: Optional[int] = None


class CreateUserBody(BaseModel):
    username: str
    password: str
    credits: int = 10


# ----------------------------------------------------------------- pages --

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # The UI is a single HTML file with inline JavaScript. Prevent browsers
    # from keeping an old auth form after a deploy/restart.
    return HTMLResponse(
        (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ------------------------------------------------------------------ auth --

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?\d[\d\s.-]{7,14}\d$")


def _classify_identifier(identifier: str) -> tuple[str, str]:
    """Return (kind, normalized_value) where kind is 'email', 'phone', or
    'username' â€” lets one input box on the login/register form accept any
    of the three, and tells the register endpoint which DB column to use."""
    value = identifier.strip()
    if _EMAIL_RE.match(value):
        return "email", value.lower()
    digits_only = re.sub(r"[\s.-]", "", value)
    if _PHONE_RE.match(value):
        return "phone", digits_only
    return "username", value


def _unique_username_from(base: str) -> str:
    """Generate a unique username from a base string by appending a number if needed."""
    # Normalize the base username
    username = base.strip().lower()
    # Remove invalid characters
    username = re.sub(r"[^a-z0-9_]", "_", username)
    # Ensure it's not empty
    if not username:
        username = "user"
    
    # Check if username exists and append number if needed
    counter = 1
    unique_username = username
    while store.get_user_by_username(unique_username):
        unique_username = f"{username}{counter}"
        counter += 1
    
    return unique_username


def _login_response(user_id: int) -> JSONResponse:
    resp = JSONResponse({"ok": True, "user_id": user_id})
    resp.set_cookie(COOKIE_NAME, create_session_cookie_value(user_id), httponly=True, samesite="lax")
    return resp


@app.post("/api/register")
def register(body: RegisterBody):
    """
    Self-service registration via email or phone number + password.

    The very FIRST account ever created on this server (by any method â€”
    this form, or a "Sign in with ..." button) becomes the admin. After
    that, further self-registration is allowed by default (see
    OPEN_REGISTRATION) so multiple people can sign up on their own;
    set OPEN_REGISTRATION=false in .env to go back to "admin creates every
    account by hand" instead.
    """
    is_first_user = not store.any_users_exist()
    if not is_first_user and not OPEN_REGISTRATION:
        raise HTTPException(
            403,
            "ÄÄƒng kÃ½ Ä‘ang bá»‹ khoÃ¡ bá»Ÿi quáº£n trá»‹ viÃªn. LiÃªn há»‡ admin Ä‘á»ƒ Ä‘Æ°á»£c cáº¥p tÃ i khoáº£n.",
        )
    if len(body.password) < 8:
        raise HTTPException(400, "Máº­t kháº©u cáº§n tá»‘i thiá»ƒu 8 kÃ½ tá»±")

    username = body.username.strip()
    if len(username) < 3:
        raise HTTPException(400, "TÃªn Ä‘Äƒng nháº­p cáº§n tá»‘i thiá»ƒu 3 kÃ½ tá»±")
    if _classify_identifier(username)[0] != "username":
        raise HTTPException(400, "TÃªn Ä‘Äƒng nháº­p khÃ´ng Ä‘Æ°á»£c lÃ  email hoáº·c sá»‘ Ä‘iá»‡n thoáº¡i")
    if store.get_user_by_identifier(username):
        raise HTTPException(409, "TÃªn Ä‘Äƒng nháº­p nÃ y Ä‘Ã£ Ä‘Æ°á»£c sá»­ dá»¥ng")

    kind, value = _classify_identifier(body.contact_identifier)
    if kind not in ("email", "phone"):
        raise HTTPException(400, "Vui lÃ²ng nháº­p Ä‘Ãºng email hoáº·c sá»‘ Ä‘iá»‡n thoáº¡i")
    if store.get_user_by_identifier(value):
        raise HTTPException(409, "Email/sá»‘ Ä‘iá»‡n thoáº¡i nÃ y Ä‘Ã£ Ä‘Æ°á»£c Ä‘Äƒng kÃ½")
    email = value if kind == "email" else None
    phone = value if kind == "phone" else None

    referrer = None
    if body.referral_code and body.referral_code.strip():
        referrer = store.get_user_by_referral_code(body.referral_code.strip())
        if referrer is None:
            raise HTTPException(400, "MÃ£ giá»›i thiá»‡u khÃ´ng há»£p lá»‡")

    user_id = store.create_user(
        username, hash_password(body.password),
        is_admin=is_first_user, credits=10_000 if is_first_user else 10,
        email=email, phone=phone,
        referred_by_user_id=referrer["id"] if referrer else None,
    )
    if referrer is not None:
        # Both sides get a bonus â€” the invitee starts with extra credit
        # instead of the usual 10, and the person who invited them gets
        # rewarded too, same moment their friend actually signs up (not
        # requiring the friend to do anything further first).
        store.adjust_credits(user_id, REFERRAL_BONUS_CREDITS)
        store.adjust_credits(referrer["id"], REFERRAL_BONUS_CREDITS)
    return _login_response(user_id)


@app.get("/api/bootstrap")
def bootstrap():
    """Tells the frontend whether to show 'register first admin' or 'login',
    and which identity providers are configured (so it only shows working
    'Sign in with ...' buttons)."""
    return {
        "needs_registration": not store.any_users_exist(),
        "open_registration": OPEN_REGISTRATION,
        "identity_providers": {
            name: identity_oauth.get_identity_oauth_client(name).is_configured()
            for name in ("google", "github", "facebook")
        },
    }


@app.post("/api/login")
def login(body: LoginBody):
    _kind, identifier = _classify_identifier(body.identifier)
    user = store.get_user_by_identifier(identifier)
    if not user or not user["password_hash"] or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Sai thÃ´ng tin Ä‘Äƒng nháº­p hoáº·c máº­t kháº©u")
    return _login_response(user["id"])


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/api/me")
def me(user_id: int = Depends(get_current_user_id)):
    user = store.get_user_by_id(user_id)
    referral_code = user["referral_code"] or store.ensure_referral_code(user_id)
    return {
        "id": user["id"], "username": user["username"],
        "email": user["email"], "phone": user["phone"],
        "credits": user["credits"], "is_admin": bool(user["is_admin"]),
        "referral_code": referral_code,
    }


@app.get("/api/stats/me")
def stats_me(user_id: int = Depends(get_current_user_id)):
    """Personal usage stats for the logged-in user â€” powers the small
    stats widget above their own history (not the admin-only site-wide
    stats at /api/admin/stats)."""
    return store.user_stats(user_id)


@app.post("/api/top-up-requests")
def create_top_up_request(body: TopUpRequestBody, user_id: int = Depends(get_current_user_id)):
    if TOP_UP_PACKAGES.get(body.credits) != body.amount_vnd:
        raise HTTPException(400, "GÃ³i náº¡p khÃ´ng há»£p lá»‡")
    request_id = store.create_top_up_request(
        user_id,
        body.credits,
        body.amount_vnd,
        body.payment_method.strip()[:40] or "bank_transfer",
        note=(body.note or "").strip()[:1000] or None,
    )
    transfer_content = f"UVAI {user_id} {request_id}"
    return {
        "ok": True, "id": request_id, "transfer_content": transfer_content,
        "payment": _payment_config(body.amount_vnd, transfer_content),
    }


@app.get("/api/payment-config")
def payment_config(user_id: int = Depends(get_current_user_id)):
    """Public-to-authenticated-users payment destination (never secrets)."""
    return _payment_config()


@app.get("/api/top-up-requests")
def list_my_top_up_requests(user_id: int = Depends(get_current_user_id)):
    return [
        {
            "id": r["id"], "credits": r["credits"], "amount_vnd": r["amount_vnd"],
            "payment_method": r["payment_method"], "note": r["note"],
            "status": r["status"], "admin_note": r["admin_note"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
        for r in store.list_top_up_requests_for_user(user_id)
    ]


# --------------------------------------------------- identity oauth (SSO) --

def _identity_redirect_uri(request: Request, provider: str) -> str:
    return str(request.base_url).rstrip("/") + f"/api/identity/callback/{provider}"


@app.get("/api/identity/login/{provider}")
def identity_login(provider: str, request: Request):
    """Returns the URL to send the whole browser tab to (a full-page
    redirect, not a popup) to start "Sign in with {provider}"."""
    try:
        client = identity_oauth.get_identity_oauth_client(provider)
    except ValueError:
        raise HTTPException(404, "NhÃ  cung cáº¥p Ä‘Äƒng nháº­p khÃ´ng Ä‘Æ°á»£c há»— trá»£")
    if not client.is_configured():
        raise HTTPException(400, client.not_configured_message())

    state = identity_oauth.new_state()
    store.create_identity_oauth_state(state, provider)
    url = client.authorize_url(_identity_redirect_uri(request, provider), state)
    return {"authorize_url": url}


@app.get("/api/identity/callback/{provider}")
def identity_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    """
    Browser lands here after approving (or denying) sign-in on the
    provider's own consent screen. Finds-or-creates the local account and
    sets the session cookie, then redirects back to the app's home page â€”
    unlike `/api/social/callback/...` (which closes a popup window), this
    IS the main tab, since signing in is the primary action, not a
    secondary "connect while already using the app" one.
    """
    if error:
        return RedirectResponse(url=f"/?login_error={urllib.parse.quote(error)}")

    state_row = store.consume_identity_oauth_state(state)
    if not state_row or state_row["provider"] != provider:
        return RedirectResponse(url="/?login_error=state_invalid")

    try:
        client = identity_oauth.get_identity_oauth_client(provider)
        result = client.exchange_code(code, _identity_redirect_uri(request, provider))
    except Exception:
        logger.exception("Identity OAuth callback failed for provider=%s", provider)
        return RedirectResponse(url="/?login_error=exchange_failed")

    user = store.get_user_by_oauth(provider, result.provider_user_id)
    if user is None and result.email:
        # Someone who already has a password account with this email signs
        # in with Google/etc. for the first time â€” link it to the same
        # account rather than creating a confusing duplicate.
        user = store.get_user_by_email(result.email)
    if user is None:
        is_first_user = not store.any_users_exist()
        username_base = result.display_name or result.email or f"{provider}_{result.provider_user_id[:8]}"
        username = _unique_username_from(username_base)
        user_id = store.create_user_oauth(
            username, provider, result.provider_user_id,
            email=result.email, is_admin=is_first_user,
            credits=10_000 if is_first_user else 10,
        )
    else:
        user_id = user["id"]

    resp = RedirectResponse(url="/")
    resp.set_cookie(COOKIE_NAME, create_session_cookie_value(user_id), httponly=True, samesite="lax")
    return resp


# ------------------------------------------------------------------ jobs --

def _build_service_for_job(job):
    """Shared service-construction logic for both the normal (straight-
    through) job path and the resume-after-review render path â€” both need
    the exact same source/OCR-language and logo/voice settings."""
    # source_language "auto" -> both transcription_language=None (Whisper
    # auto-detects the spoken language) and ocr_languages left at the
    # "auto" sentinel (resolved later from whatever Whisper detected).
    # An explicit language pins both to that language directly.
    is_auto_source = not job.source_language or job.source_language == "auto"
    transcription_language = None if is_auto_source else job.source_language
    ocr_languages = (
        ocr_language_map.AUTO_OCR_SENTINEL if is_auto_source
        else ocr_language_map.OCR_LANGUAGE_MAP.get(job.source_language, ("en",))
    )

    render_config = RenderConfig(
        preset=WEB_RENDER_PRESET,
        timeout_seconds=WEB_RENDER_TIMEOUT_SECONDS,
    )
    
    # Build animated subtitle config if provided
    animated_subtitle_config = None
    if job.animated_subtitle_config and job.animated_subtitle_config.get("enabled"):
        try:
            effect_name = job.animated_subtitle_config.get("effect", "none")
            effect = SubtitleEffect(effect_name) if effect_name else SubtitleEffect.NONE
            
            style_data = job.animated_subtitle_config.get("style", {})
            style = SubtitleStyle(
                font_size=style_data.get("font_size", 24),
                font_color=style_data.get("font_color", "white"),
                background_color=style_data.get("background_color", "black@0.5"),
            )
            
            effect_params = job.animated_subtitle_config.get("effect_params", {})
            
            animated_subtitle_config = AnimatedSubtitleConfig(
                enabled=True,
                effect=effect,
                style=style,
                effect_params=effect_params,
            )
        except Exception as e:
            logger.warning(f"Failed to parse animated_subtitle_config: {e}")
    
    # Build video template config if provided
    video_template_config = None
    if job.video_template_config and job.video_template_config.get("enabled"):
        try:
            video_template_config = VideoTemplateConfig(
                enabled=True,
                template=job.video_template_config.get("template", "minimal"),
                transition=job.video_template_config.get("transition", "fade"),
                color_effect=job.video_template_config.get("color_effect", "none"),
                audio_filters=job.video_template_config.get("audio_filters", {}),
                video_quality=job.video_template_config.get("video_quality", "medium"),
            )
        except Exception as e:
            logger.warning(f"Failed to parse video_template_config: {e}")
    
    if job.logo_path and Path(job.logo_path).exists():
        render_config = RenderConfig(
            preset=WEB_RENDER_PRESET,
            timeout_seconds=WEB_RENDER_TIMEOUT_SECONDS,
            logo_path=job.logo_path,
            logo_corner=job.logo_corner or "bottom_right",
            logo_size_px=job.logo_size_px or 120,
            animated_subtitle_config=animated_subtitle_config,
            video_template_config=video_template_config,
        )
    else:
        render_config = RenderConfig(
            preset=WEB_RENDER_PRESET,
            timeout_seconds=WEB_RENDER_TIMEOUT_SECONDS,
            animated_subtitle_config=animated_subtitle_config,
            video_template_config=video_template_config,
        )

    return create_localization_service(
        run_transcription=True,
        transcription_language=transcription_language,
        run_translation=True,
        target_language=job.target_language,
        run_tts=True,
        tts_voice=job.tts_voice,
        generate_subtitles=True,
        mix_audio=True,
        replace_source_audio=os.getenv("COPYRIGHT_SAFE_AUDIO", "true").lower() in {"1", "true", "yes", "on"},
        background_music_dir=Path(os.getenv("LICENSED_MUSIC_DIR", "./local_data/music")),
        replacement_music_volume=float(os.getenv("REPLACEMENT_MUSIC_VOLUME", "0.16")),
        render_video=True,
        render_config=render_config,
        enable_text_cover=True,
        ocr_languages=ocr_languages,
        logger=logger,
        progress_callback=lambda percent, message: store.update_job(
            job.id, progress_note=f"[{percent}%] {message}"
        ),
    )


async def _run_job(job_id: str) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Äang táº£i video...")
        service = _build_service_for_job(job)
        job_output_dir = _OUTPUT_BASE_DIR / "web_jobs" / job_id

        if job.review_mode:
            # Stop after translation and wait for the person to review/edit
            # the translated sentences via PUT .../segments, then
            # POST .../render (-> _run_render_from_review) to continue.
            store.update_job(job_id, progress_note="Äang dá»‹ch phá»¥ Ä‘á» Ä‘á»ƒ báº¡n xem trÆ°á»›c...")
            prepared = await service.prepare_for_review(
                job.source_url, job_output_dir, target_language=job.target_language
            )
            segments = prepared.translated_segments or [
                {"start": 0.0, "end": 0.0, "text": prepared.translated_text or ""}
            ]
            segments_payload = (
                [{"start": s.start, "end": s.end, "text": s.text} for s in prepared.translated_segments]
                if prepared.translated_segments else segments
            )
            store.set_job_segments(job_id, segments_payload)
            store.set_job_review_state(job_id, prepared_localization_to_dict(prepared))
            store.update_job(
                job_id, status="review",
                progress_note="ÄÃ£ dá»‹ch xong â€” chá»‰nh sá»­a phá»¥ Ä‘á» rá»“i báº¥m Render",
            )
            return

        store.update_job(job_id, progress_note="Äang xá»­ lÃ½ (dá»‹ch, lá»“ng tiáº¿ng, render)...")
        result = await service.localize(job.source_url, job_output_dir, target_language=job.target_language)
        _finish_job_from_result(job_id, job, result)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        store.update_job(job_id, status="error", error=f"{exc}\n{traceback.format_exc()[-1500:]}")
        _refund_job_credits(job)
    finally:
        _running_tasks.pop(job_id, None)


def _finish_job_from_result(job_id: str, job, result) -> None:
    if result.final_video_path and Path(result.final_video_path).exists():
        title = (result.translated_text or job.source_url)[:80]
        store.update_job(
            job_id, status="done", progress_note="HoÃ n táº¥t",
            final_video_path=str(result.final_video_path), title=title,
        )
        # Best-effort automated sanity check (quiet audio, wrong duration).
        # Never fails the job over this â€” it's an informational warning
        # badge in the UI, not a hard gate on publishing.
        try:
            source_duration = None
            if result.audio_pipeline_result and result.audio_pipeline_result.audio_result:
                source_duration = result.audio_pipeline_result.audio_result.duration
            warnings = analyze_output_quality(Path(result.final_video_path), source_duration=source_duration)
            if warnings:
                store.set_job_qc_warnings(job_id, warnings)
        except Exception:
            logger.exception("Quality check failed for job %s (non-fatal)", job_id)
    else:
        store.update_job(job_id, status="error", error="KhÃ´ng táº¡o Ä‘Æ°á»£c video Ä‘áº§u ra (final_video_path rá»—ng)")
        _refund_job_credits(job)


async def _run_render_from_review(job_id: str) -> None:
    """Resume a job sitting at status='review': re-hydrate what
    prepare_for_review() produced, and render using whatever's currently in
    segments_json (the person's edits, if they made any â€” otherwise still
    the original machine translation, unedited)."""
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Äang lá»“ng tiáº¿ng vÃ  render...")
        service = _build_service_for_job(job)

        review_state = json.loads(job.review_state_json)
        prepared = prepared_localization_from_dict(review_state)

        segments_data = json.loads(job.segments_json) if job.segments_json else []
        edited_segments = [
            TranscriptSegment(start=s["start"], end=s["end"], text=s["text"]) for s in segments_data
        ] or None

        result = await service.finalize_from_review(prepared, edited_segments)
        _finish_job_from_result(job_id, job, result)
    except Exception as exc:
        logger.exception("Job %s failed to render from review", job_id)
        store.update_job(job_id, status="error", error=f"{exc}\n{traceback.format_exc()[-1500:]}")
        _refund_job_credits(job)
    finally:
        _running_tasks.pop(job_id, None)


def _refund_job_credits(job) -> None:
    """A job that errors out shouldn't cost the user credit â€” refund it."""
    if JOB_COST_CREDITS > 0:
        try:
            store.adjust_credits(job.user_id, JOB_COST_CREDITS)
        except Exception:
            logger.exception("Failed to refund credits for job %s", job.id)


def _creator_scene_brief_from_topic(topic: str, language: str) -> List[str]:
    topic = topic.strip()
    subject, entity_kind = _creator_topic_subject(topic, language)
    if entity_kind == "animal":
        if language == "vi":
            return [
                f"ToÃ n cáº£nh {subject} trong mÃ´i trÆ°á»ng sá»‘ng tá»± nhiÃªn",
                f"Cáº­n cáº£nh khuÃ´n máº·t vÃ  Ä‘áº·c Ä‘iá»ƒm cÆ¡ thá»ƒ cá»§a {subject}",
                f"{subject.capitalize()} di chuyá»ƒn trong tá»± nhiÃªn",
                f"{subject.capitalize()} tÃ¬m kiáº¿m thá»©c Äƒn",
                f"Cáº­n cáº£nh táº­p tÃ­nh tá»± nhiÃªn ná»•i báº­t cá»§a {subject}",
                f"{subject.capitalize()} pháº£n á»©ng vá»›i má»™t má»‘i Ä‘e dá»a trong tá»± nhiÃªn",
                f"{subject.capitalize()} tÆ°Æ¡ng tÃ¡c vá»›i mÃ´i trÆ°á»ng sá»‘ng xung quanh",
                f"GÃ³c rá»™ng theo chÃ¢n {subject} trong mÃ´i trÆ°á»ng hoang dÃ£",
                f"Cáº­n cáº£nh má»™t Ä‘áº·c Ä‘iá»ƒm Ã­t ngÆ°á»i biáº¿t cá»§a {subject}",
                f"Cáº£nh káº¿t thÃºc vá»›i {subject} rá»i Ä‘i trong tá»± nhiÃªn",
            ]
        return [
            f"Wide shot of {subject} in its natural habitat",
            f"Close-up of the face and physical features of {subject}",
            f"{subject.capitalize()} moving through the wild",
            f"{subject.capitalize()} searching for food",
            f"Close-up of a distinctive natural behavior of {subject}",
            f"{subject.capitalize()} reacting to a threat in the wild",
            f"{subject.capitalize()} interacting with its habitat",
            f"Wide tracking shot following {subject} in the wild",
            f"Close-up illustrating a little-known feature of {subject}",
            f"Closing shot of {subject} walking away in nature",
        ]
    profile = _creator_stock_profile(topic)
    if profile["category"] == "beauty":
        if language == "vi":
            return [
                f"ChÃ¢n dung ngÆ°á»i phá»¥ ná»¯ vá»›i lÃ n da tá»± nhiÃªn, Ã¡nh sÃ¡ng má»m, chá»§ Ä‘á» {topic}",
                "Cáº­n cáº£nh quy trÃ¬nh chÄƒm sÃ³c da, thoa serum lÃªn khuÃ´n máº·t sáº¡ch",
                "CÃ¡c sáº£n pháº©m má»¹ pháº©m vÃ  skincare Ä‘Æ°á»£c sáº¯p xáº¿p Ä‘áº¹p trÃªn bÃ n trang Ä‘iá»ƒm",
                "ChuyÃªn viÃªn trang Ä‘iá»ƒm Ä‘ang sá»­ dá»¥ng cá» vÃ  má»¹ pháº©m cho khÃ¡ch hÃ ng",
                "NgÆ°á»i phá»¥ ná»¯ rá»­a máº·t vÃ  thá»±c hiá»‡n routine dÆ°á»¡ng da buá»•i sÃ¡ng",
                "Cáº­n cáº£nh lÃ n da khá»e, lá»›p makeup tá»± nhiÃªn vÃ  ná»¥ cÆ°á»i tá»± tin",
                "KhÃ´ng gian spa hoáº·c beauty salon sáº¡ch sáº½, thÆ° giÃ£n vÃ  sang trá»ng",
                "Cáº­n cáº£nh son mÃ´i, pháº¥n ná»n, mascara vÃ  dá»¥ng cá»¥ trang Ä‘iá»ƒm",
                "NgÆ°á»i dÃ¹ng soi gÆ°Æ¡ng sau khi hoÃ n thÃ nh quy trÃ¬nh lÃ m Ä‘áº¹p",
                "Káº¿t quáº£ trÆ°á»›c vÃ  sau khi chÄƒm sÃ³c da, phong thÃ¡i tá»± tin, ráº¡ng rá»¡",
            ]
        return [f"Natural beauty portrait in soft light, topic {topic}", *profile["queries"][1:]]
    if language == "vi":
        return [
            f"ToÃ n cáº£nh giá»›i thiá»‡u trá»±c quan vá» {topic}", f"Cáº­n cáº£nh chi tiáº¿t quan trá»ng nháº¥t cá»§a {topic}",
            f"Má»™t ngÆ°á»i Ä‘ang trá»±c tiáº¿p tráº£i nghiá»‡m hoáº·c thá»±c hiá»‡n {topic}", f"CÃ¡c cÃ´ng cá»¥ vÃ  váº­t dá»¥ng liÃªn quan Ä‘áº¿n {topic}",
            f"Quy trÃ¬nh thá»±c hiá»‡n {topic} theo tá»«ng bÆ°á»›c", f"GÃ³c quay cáº­n cáº£nh thá»ƒ hiá»‡n cháº¥t liá»‡u vÃ  chi tiáº¿t cá»§a {topic}",
            f"Bá»‘i cáº£nh Ä‘á»i thá»±c nÆ¡i {topic} thÆ°á»ng diá»…n ra", f"Káº¿t quáº£ trÆ°á»›c vÃ  sau khi Ã¡p dá»¥ng {topic}",
            f"NgÆ°á»i dÃ¹ng hÃ i lÃ²ng vá»›i káº¿t quáº£ cá»§a {topic}", f"Cáº£nh káº¿t thÃºc Ä‘áº¹p vÃ  tÃ­ch cá»±c liÃªn quan trá»±c tiáº¿p Ä‘áº¿n {topic}",
        ]
    return [
        f"Wide establishing shot visually introducing {topic}", f"Close-up of the most important detail of {topic}",
        f"A person directly experiencing or doing {topic}", f"Tools and objects directly related to {topic}",
        f"Step by step process of {topic}", f"Detailed close-up showing the texture and features of {topic}",
        f"Real-life environment where {topic} happens", f"Before and after result of {topic}",
        f"A person satisfied with the result of {topic}", f"Positive cinematic closing shot directly related to {topic}",
    ]


def _creator_script_text_from_topic(topic: str, language: str, duration_seconds: int = 30) -> str:
    duration = max(10, min(1200, int(duration_seconds or 30)))
    seconds_per_scene = 5 if duration <= 60 else 8
    scene_count = max(4, min(150, round(duration / seconds_per_scene)))
    base = _creator_scene_brief_from_topic(topic, language)
    scenes = list(base[:scene_count])
    while len(scenes) < scene_count:
        source = base[len(scenes) % len(base)]
        prefix = "Cáº£nh bá»• sung" if language == "vi" else "Additional scene"
        scenes.append(f"{prefix} {len(scenes) + 1}: {source}")
    return "\n".join(scenes)


def _creator_narration_from_topic(topic: str, language: str) -> List[str]:
    topic = topic.strip()
    subject, entity_kind = _creator_topic_subject(topic, language)
    if entity_kind == "animal":
        if language == "vi":
            return [
                f"Báº¡n nghÄ© mÃ¬nh Ä‘Ã£ biáº¿t rÃµ vá» {subject} chÆ°a?",
                f"Video nÃ y sáº½ khÃ¡m phÃ¡ nhá»¯ng Ä‘áº·c Ä‘iá»ƒm Ã­t ngÆ°á»i biáº¿t cá»§a {subject}.",
                f"TrÆ°á»›c háº¿t, hÃ£y quan sÃ¡t hÃ¬nh dÃ¡ng vÃ  cÃ¡ch {subject} thÃ­ch nghi vá»›i mÃ´i trÆ°á»ng sá»‘ng.",
                f"Táº­p tÃ­nh kiáº¿m Äƒn cá»§a {subject} cÅ©ng hÃ© lá»™ nhiá»u kháº£ nÄƒng Ä‘Ã¡ng chÃº Ã½.",
                f"Khi gáº·p nguy hiá»ƒm, {subject} cÃ³ nhá»¯ng pháº£n á»©ng sinh tá»“n ráº¥t Ä‘áº·c trÆ°ng.",
                f"Má»—i Ä‘áº·c Ä‘iá»ƒm cáº§n Ä‘Æ°á»£c nhÃ¬n trong Ä‘Ãºng mÃ´i trÆ°á»ng tá»± nhiÃªn cá»§a loÃ i váº­t nÃ y.",
                f"Nhá» váº­y, chÃºng ta hiá»ƒu {subject} chÃ­nh xÃ¡c hÆ¡n thay vÃ¬ chá»‰ dá»±a vÃ o tÃªn gá»i.",
                f"Báº¡n áº¥n tÆ°á»£ng nháº¥t vá»›i Ä‘áº·c Ä‘iá»ƒm nÃ o cá»§a {subject}?",
                "HÃ£y Ä‘á»ƒ láº¡i bÃ¬nh luáº­n vÃ  theo dÃµi Ä‘á»ƒ khÃ¡m phÃ¡ thÃªm vá» tháº¿ giá»›i Ä‘á»™ng váº­t.",
            ]
        return [
            f"How well do you really know {subject}?",
            f"This video explores little-known characteristics of {subject}.",
            f"First, notice how {subject} is built and adapted to its habitat.",
            f"Its feeding behavior reveals more remarkable abilities.",
            f"When danger appears, {subject} shows distinctive survival responses.",
            f"Each trait makes more sense in the animal's natural environment.",
            f"That context helps us understand {subject} beyond its name.",
            f"Which characteristic of {subject} surprised you most?",
            "Leave a comment and follow for more wildlife discoveries.",
        ]
    if language == "vi":
        return [
            f"Báº¡n Ä‘ang quan tÃ¢m Ä‘áº¿n {topic}?",
            f"Trong video nÃ y, chÃºng ta sáº½ tÃ¬m hiá»ƒu nhanh vá» {topic}.",
            "Äiá»u quan trá»ng Ä‘áº§u tiÃªn lÃ  xÃ¡c Ä‘á»‹nh má»¥c tiÃªu báº¡n thá»±c sá»± muá»‘n Ä‘áº¡t Ä‘Æ°á»£c.",
            "Tiáº¿p theo, hÃ£y chia má»¥c tiÃªu lá»›n thÃ nh nhá»¯ng bÆ°á»›c nhá» vÃ  dá»… thá»±c hiá»‡n.",
            "Báº¡n nÃªn Æ°u tiÃªn cÃ¡c cÃ´ng cá»¥ Ä‘Æ¡n giáº£n, phÃ¹ há»£p vá»›i nhu cáº§u cá»§a mÃ¬nh.",
            "HÃ£y thá»­ nghiá»‡m tá»«ng bÆ°á»›c vÃ  ghi láº¡i káº¿t quáº£ Ä‘á»ƒ biáº¿t Ä‘iá»u gÃ¬ hiá»‡u quáº£.",
            "Äá»«ng quÃªn kiá»ƒm tra nguá»“n thÃ´ng tin trÆ°á»›c khi Ä‘Æ°a ra quyáº¿t Ä‘á»‹nh.",
            "Khi Ä‘Ã£ quen, báº¡n cÃ³ thá»ƒ tá»‘i Æ°u quy trÃ¬nh Ä‘á»ƒ tiáº¿t kiá»‡m nhiá»u thá»i gian hÆ¡n.",
            f"Chá»‰ cáº§n báº¯t Ä‘áº§u tá»« má»™t bÆ°á»›c nhá», {topic} sáº½ trá»Ÿ nÃªn dá»… tiáº¿p cáº­n hÆ¡n.",
            "Náº¿u tháº¥y ná»™i dung há»¯u Ã­ch, hÃ£y lÆ°u video vÃ  theo dÃµi Ä‘á»ƒ xem thÃªm.",
        ]
    return [
        f"Are you interested in {topic}?", f"Here is a quick introduction to {topic}.",
        "First, decide on the result you actually want to achieve.",
        "Break the larger goal into small and practical steps.",
        "Choose simple tools that match your real needs.",
        "Test each step and keep track of what works best.",
        "Always verify your sources before making a decision.",
        "Once the basics work, optimize the process to save time.",
        f"Start with one small step and {topic} will feel much easier.",
        "Save this video and follow for more useful ideas.",
    ]


def _creator_narration_text_from_topic(topic: str, language: str, duration_seconds: int = 30) -> str:
    duration = max(10, min(1200, int(duration_seconds or 30)))
    target_words = max(25, round(duration * (2.35 if language == "vi" else 2.25)))
    subject, _ = _creator_topic_subject(topic, language)
    base = _creator_narration_from_topic(topic, language)
    selected: List[str] = []
    for line in base:
        if selected and sum(len(item.split()) for item in selected) >= target_words * 0.95:
            break
        selected.append(line)
    additions = (
        [
            f"Tiáº¿p theo, hÃ£y xem xÃ©t má»™t khÃ­a cáº¡nh khÃ¡c cá»§a {subject} trong bá»‘i cáº£nh thá»±c táº¿.",
            f"Chi tiáº¿t nÃ y giÃºp chÃºng ta hiá»ƒu Ä‘áº§y Ä‘á»§ vÃ  chÃ­nh xÃ¡c hÆ¡n vá» {subject}.",
            f"Khi ghÃ©p cÃ¡c Ä‘áº·c Ä‘iá»ƒm láº¡i vá»›i nhau, cÃ¢u chuyá»‡n vá» {subject} trá»Ÿ nÃªn rÃµ rÃ ng hÆ¡n.",
        ]
        if language == "vi" else
        [
            f"Next, consider another aspect of {subject} in its real context.",
            f"This detail gives us a fuller and more accurate understanding of {subject}.",
            f"Together, these characteristics make the story of {subject} much clearer.",
        ]
    )
    index = 0
    while sum(len(item.split()) for item in selected) < target_words * 0.95:
        selected.append(additions[index % len(additions)])
        index += 1
    return "\n".join(selected)


def _dedupe_clean_terms(terms: List[str], limit: int = 18) -> List[str]:
    result: List[str] = []
    seen = set()
    for term in terms:
        cleaned = re.sub(r"\s+", " ", str(term or "").strip().strip("#,.;:"))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned[:100])
        if len(result) >= limit:
            break
    return result


def _ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").lower().replace("Ä‘", "d"))
    return normalized.encode("ascii", "ignore").decode("ascii")


def _creator_seo_keywords_from_topic(
    topic: str,
    language: str,
    duration_seconds: int = 30,
    existing: Optional[List[str]] = None,
) -> List[str]:
    subject, entity_kind = _creator_topic_subject(topic, language)
    legacy_base = _creator_keywords_from_topic(topic, language, duration_seconds)
    base = legacy_base if entity_kind == "animal" else [subject]
    if language == "vi":
        if entity_kind == "animal":
            seo_terms = [
                f"{subject}",
                f"{subject} la con gi",
                f"dac diem {subject}",
                f"{subject} song o dau",
                f"{subject} an gi",
                f"su that ve {subject}",
                f"{subject} co nguy hiem khong",
                f"nhung dieu it biet ve {subject}",
                f"{subject} ngoai tu nhien",
                f"video giai thich ve {subject}",
            ]
        else:
            seo_terms = [
                f"cach {subject}",
                f"{subject} cho nguoi moi",
                f"{subject} tung buoc",
                f"sai lam khi {subject}",
                f"meo {subject}",
                f"huong dan {subject}",
                f"review {subject}",
                f"so sanh {subject}",
                f"{subject} co dang lam khong",
                f"{subject} nhanh va de hieu",
                f"{subject} cho TikTok",
                f"{subject} cho YouTube Shorts",
            ]
    else:
        if entity_kind == "animal":
            seo_terms = [
                subject,
                f"what is {subject}",
                f"{subject} facts",
                f"{subject} habitat",
                f"what does {subject} eat",
                f"little known facts about {subject}",
                f"is {subject} dangerous",
                f"{subject} explained",
            ]
        else:
            seo_terms = [
                f"how to {subject}",
                f"{subject} for beginners",
                f"{subject} step by step",
                f"{subject} mistakes",
                f"{subject} tips",
                f"{subject} tutorial",
                f"{subject} review",
                f"{subject} comparison",
                f"is {subject} worth it",
                f"{subject} for TikTok",
                f"{subject} for YouTube Shorts",
            ]
    return _dedupe_clean_terms([*(existing or []), *base, *seo_terms], 18)


def _creator_hook_line(topic: str, language: str) -> str:
    subject, entity_kind = _creator_topic_subject(topic, language)
    if language == "vi":
        if entity_kind == "animal":
            return f"Bạn có chắc mình đã hiểu đúng về {subject} không?"
        return f"Nếu bạn đang muốn làm {subject}, đừng bắt đầu trước khi biết điểm này."
    if entity_kind == "animal":
        return f"Do you really know what makes {subject} different?"
    return f"If you want to do {subject}, do not start before you know this."


def _is_weak_creator_hook(line: str, language: str) -> bool:
    text = re.sub(r"\s+", " ", _ascii_fold(line).strip())
    if not text:
        return True
    weak_starts = [
        "trong video nay", "hom nay", "xin chao", "chung ta se", "ban dang quan tam",
        "in this video", "today we", "hello", "here is a quick introduction", "are you interested",
    ]
    return any(text.startswith(start) for start in weak_starts)


def _creator_target_narration_words(language: str, duration_seconds: int) -> int:
    duration = max(10, min(1200, int(duration_seconds or 30)))
    return max(25, round(duration * (2.35 if language == "vi" else 2.25)))


def _creator_narration_padding_lines(topic: str, language: str) -> List[str]:
    subject, entity_kind = _creator_topic_subject(topic, language)
    if language == "vi":
        if entity_kind == "animal":
            return [
                f"Äiá»ƒm Ä‘Ã¡ng chÃº Ã½ lÃ  {subject} chá»‰ tháº­t sá»± dá»… hiá»ƒu khi nhÃ¬n trong mÃ´i trÆ°á»ng sá»‘ng tá»± nhiÃªn.",
                f"Chi tiáº¿t nÃ y giÃºp ngÆ°á»i xem phÃ¢n biá»‡t {subject} vá»›i nhá»¯ng loÃ i hoáº·c khÃ¡i niá»‡m dá»… bá»‹ nháº§m láº«n.",
                f"Náº¿u báº¡n tháº¥y pháº§n nÃ y há»¯u Ã­ch, hÃ£y lÆ°u láº¡i Ä‘á»ƒ xem tiáº¿p cÃ¡c Ä‘áº·c Ä‘iá»ƒm cÃ²n láº¡i rÃµ hÆ¡n.",
            ]
        return [
            f"Äiá»ƒm quan trá»ng lÃ  hÃ£y báº¯t Ä‘áº§u tá»« má»™t bÆ°á»›c nhá» cá»§a {subject}, rá»“i Ä‘o káº¿t quáº£ tháº­t.",
            f"Khi lÃ m Ä‘Ãºng trÃ¬nh tá»±, báº¡n sáº½ tháº¥y pháº§n khÃ³ nháº¥t thÆ°á»ng náº±m á»Ÿ cÃ¡ch chuáº©n bá»‹, khÃ´ng pháº£i cÃ´ng cá»¥.",
            f"Náº¿u muá»‘n Ã¡p dá»¥ng ngay, hÃ£y lÆ°u video nÃ y vÃ  thá»­ láº¡i vá»›i workflow cá»§a chÃ­nh báº¡n.",
        ]
    if entity_kind == "animal":
        return [
            f"The key detail is easier to understand when {subject} is shown in its real habitat.",
            f"This context helps viewers separate {subject} from similar names or misleading assumptions.",
            "Save this video if you want the next traits explained with the same clarity.",
        ]
    return [
        f"The practical move is to start with one small part of {subject}, then measure the real result.",
        "When the order is clear, the hard part is usually preparation rather than the tool itself.",
        "Save this video and test the workflow on your own process.",
    ]


def _postprocess_creator_narration(
    narration_script: str, topic: str, language: str, duration_seconds: int,
) -> str:
    lines = [line.strip() for line in str(narration_script or "").splitlines() if line.strip()]
    hook = _creator_hook_line(topic, language)
    if not lines:
        lines = [hook]
    if _is_weak_creator_hook(lines[0], language):
        lines[0] = hook
    target_words = _creator_target_narration_words(language, duration_seconds)
    minimum_words = round(target_words * 0.92)
    padding = _creator_narration_padding_lines(topic, language)
    index = 0
    while len(" ".join(lines).split()) < minimum_words:
        lines.append(padding[index % len(padding)])
        index += 1
    return "\n".join(lines)


def _postprocess_creator_visual_brief(
    visual_brief: str,
    topic: str,
    language: str,
    duration_seconds: int,
    narration_script: str,
) -> str:
    expected = max(4, min(150, round(max(10, min(1200, int(duration_seconds or 30))) / (5 if int(duration_seconds or 30) <= 60 else 8))))
    subject, entity_kind = _creator_topic_subject(topic, language)
    raw_lines = [line.strip() for line in str(visual_brief or "").splitlines() if line.strip()]
    fallback_lines = _creator_scene_brief_from_topic(topic, language)
    narration_lines = [line.strip() for line in str(narration_script or "").splitlines() if line.strip()]
    enhanced: List[str] = []
    for index in range(expected):
        source = raw_lines[index] if index < len(raw_lines) else fallback_lines[index % len(fallback_lines)]
        narration_hint = narration_lines[index] if index < len(narration_lines) else narration_lines[-1] if narration_lines else topic
        lower = source.lower()
        folded = _ascii_fold(source)
        generic_patterns = (
            "gioi thieu truc quan", "chi tiet quan trong", "boi canh doi thuc",
            "canh ket thuc dep", "positive cinematic", "visually introducing",
            "important detail", "real-life environment", "cinematic closing",
        )
        too_generic = (
            len(source.split()) < 6
            or lower in {"intro", "opening", "b-roll", "canh", "scene"}
            or any(pattern in folded for pattern in generic_patterns)
        )
        if too_generic or subject.lower() not in lower:
            if language == "vi":
                if entity_kind == "animal":
                    source = f"Cáº£nh {index + 1}: quay rÃµ {subject} trong mÃ´i trÆ°á»ng tá»± nhiÃªn, bÃ¡m vÃ o Ã½: {narration_hint}"
                else:
                    source = f"Cáº£nh {index + 1}: má»™t ngÆ°á»i thá»±c hiá»‡n {subject} vá»›i thao tÃ¡c nhÃ¬n tháº¥y rÃµ, bÃ¡m vÃ o Ã½: {narration_hint}"
            else:
                if entity_kind == "animal":
                    source = f"Scene {index + 1}: clear shot of {subject} in its natural habitat, matching this narration: {narration_hint}"
                else:
                    source = f"Scene {index + 1}: a person visibly doing {subject}, matching this narration: {narration_hint}"
        enhanced.append(source)
    return "\n".join(enhanced)


def _postprocess_creator_suggestion_quality(
    result: Dict[str, Any],
    topic: str,
    language: str,
    duration_seconds: int,
) -> Dict[str, Any]:
    upgraded = dict(result)
    existing_keywords = upgraded.get("keywords") if isinstance(upgraded.get("keywords"), list) else []
    upgraded["keywords"] = _creator_seo_keywords_from_topic(topic, language, duration_seconds, existing_keywords)
    narration = _postprocess_creator_narration(
        str(upgraded.get("narration_script") or ""), topic, language, duration_seconds,
    )
    visual = _postprocess_creator_visual_brief(
        str(upgraded.get("visual_brief") or upgraded.get("script") or ""),
        topic,
        language,
        duration_seconds,
        narration,
    )
    upgraded["narration_script"] = narration
    upgraded["visual_brief"] = visual
    upgraded["script"] = visual
    upgraded["quality_notes"] = [
        "Added retention hook to the opening line.",
        "Expanded keywords with search-intent long-tail terms.",
        "Tightened visual brief into concrete footage prompts tied to the narration.",
    ]
    return upgraded


def _available_gemini_models(api_key: str) -> List[str]:
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": api_key}, params={"pageSize": 1000}, timeout=30,
    )
    response.raise_for_status()
    available: List[str] = []
    for item in response.json().get("models", []):
        methods = item.get("supportedGenerationMethods") or []
        name = str(item.get("name", "")).removeprefix("models/")
        lowered = name.lower()
        if (
            name and "generateContent" in methods and "gemini" in lowered
            and not any(word in lowered for word in ("image", "embedding", "live", "tts", "robotics"))
        ):
            available.append(name)
    return available


def _select_gemini_models(api_key: str, configured: str) -> List[str]:
    preferred = [
        configured, "gemini-3.1-flash-lite", "gemini-3.5-flash",
        "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite",
    ]
    try:
        available = _available_gemini_models(api_key)
        selected = list(dict.fromkeys(name for name in preferred if name and name in available))
        selected.extend(name for name in available if name not in selected and "flash" in name.lower())
        if selected:
            return selected
        raise RuntimeError("API key khÃ´ng cÃ³ model Gemini há»— trá»£ generateContent")
    except Exception as exc:
        logger.warning("Could not list Gemini models; trying known model names: %s", exc)
        return list(dict.fromkeys(name for name in preferred if name))


def _creator_ai_prompt(body: CreatorSuggestionBody, require_search: bool = False) -> tuple[str, int]:
    duration = max(10, min(1200, int(body.duration_seconds or 30)))
    # Short-form uses denser cuts; long-form stays practical at roughly one
    # visual beat per 8 seconds. A 20-minute script therefore has 150 real
    # scene briefs instead of being silently truncated to twelve.
    seconds_per_scene = 5 if duration <= 60 else 8
    scene_count = max(4, min(150, round(duration / seconds_per_scene)))
    words_per_second = 2.35 if body.target_language == "vi" else 2.25
    narration_word_target = max(25, round(duration * words_per_second))
    narration_word_min = round(narration_word_target * 0.95)
    narration_word_max = round(narration_word_target * 1.05)
    language_name = LANGUAGE_LABELS.get(body.target_language, body.target_language)
    search_instruction = (
        "Báº®T BUá»˜C dÃ¹ng Google Search Ä‘á»ƒ nghiÃªn cá»©u search intent vÃ  cÃ¡c gÃ³c ná»™i dung Ä‘ang phÃ¹ há»£p."
        if require_search else
        "Táº¡o ná»™i dung SEO sÃ¡t search intent; khÃ´ng bá»‹a sá»‘ liá»‡u xu hÆ°á»›ng hoáº·c tuyÃªn bá»‘ Ä‘Ã£ tÃ¬m kiáº¿m web."
    )
    
    # Process advanced options if provided
    advanced_options = body.advanced_options or {}
    style = advanced_options.get("style", "general")
    tone = advanced_options.get("tone", "neutral")
    sentence_length = advanced_options.get("sentence_length", "medium")
    detail_level = advanced_options.get("detail_level", "standard")
    custom_instructions = advanced_options.get("custom_instructions", "")
    
    # Map style to Vietnamese descriptions
    style_map = {
        "general": "chung",
        "entertaining": "giáº£i trÃ­, vui váº», háº¥p dáº«n",
        "educational": "giÃ¡o dá»¥c, há»c thuáº­t, chia sáº» kiáº¿n thá»©c",
        "storytelling": "ká»ƒ chuyá»‡n, cÃ³ cá»‘t truyá»‡n",
        "tutorial": "hÆ°á»›ng dáº«n, tutorial, step-by-step",
        "review": "review, Ä‘Ã¡nh giÃ¡, so sÃ¡nh",
        "news": "tin tá»©c, thÃ´ng tin, cáº­p nháº­t",
        "motivational": "truyá»n cáº£m há»©ng, Ä‘á»™ng viÃªn",
    }
    
    # Map tone to Vietnamese descriptions
    tone_map = {
        "neutral": "trung tÃ­nh, khÃ¡ch quan",
        "casual": "thÃ¢n thiá»‡n, gáº§n gÅ©i, tá»± nhiÃªn",
        "formal": "trang trá»ng, chuyÃªn nghiá»‡p",
        "humorous": "hÃ i hÆ°á»›c, vui nhá»™n",
        "inspiring": "truyá»n cáº£m há»©ng, tÃ­ch cá»±c",
        "urgent": "cáº¥p bÃ¡ch, kháº©n trÆ°Æ¡ng",
    }
    
    # Map sentence length to word ranges
    sentence_length_map = {
        "short": (8, 12),
        "medium": (12, 18),
        "long": (18, 25),
    }
    
    # Map detail level to descriptions
    detail_map = {
        "minimal": "tá»‘i thiá»ƒu, táº­p trung vÃ o Ä‘iá»ƒm chÃ­nh",
        "standard": "chuáº©n, cÃ¢n báº±ng",
        "detailed": "chi tiáº¿t, cÃ³ vÃ­ dá»¥ cá»¥ thá»ƒ",
        "comprehensive": "ráº¥t chi tiáº¿t, Ä‘áº§y Ä‘á»§ thÃ´ng tin",
    }
    
    style_desc = style_map.get(style, "chung")
    tone_desc = tone_map.get(tone, "trung tÃ­nh")
    sentence_range = sentence_length_map.get(sentence_length, (12, 18))
    detail_desc = detail_map.get(detail_level, "chuáº©n")
    
    # Adjust word count based on detail level
    if detail_level == "minimal":
        narration_word_target = round(narration_word_target * 0.8)
        narration_word_min = round(narration_word_min * 0.8)
        narration_word_max = round(narration_word_max * 0.8)
    elif detail_level == "detailed":
        narration_word_target = round(narration_word_target * 1.15)
        narration_word_min = round(narration_word_min * 1.15)
        narration_word_max = round(narration_word_max * 1.15)
    elif detail_level == "comprehensive":
        narration_word_target = round(narration_word_target * 1.3)
        narration_word_min = round(narration_word_min * 1.3)
        narration_word_max = round(narration_word_max * 1.3)
    
    custom_instruction_text = f"\nYÃŠU Cáº¦U Äáº¶C BIá»†T: {custom_instructions}" if custom_instructions else ""
    
    prompt = f"""Báº¡n lÃ  biÃªn ká»‹ch video ngáº¯n vÃ  chuyÃªn gia SEO YouTube/TikTok.
{search_instruction}
HÃ£y láº­p ná»™i dung cho video vá»›i thÃ´ng sá»‘ báº¯t buá»™c:
- Chá»§ Ä‘á»: {body.topic.strip()}
- NgÃ´n ngá»¯ Ä‘áº§u ra: {language_name}
- Tá»· lá»‡ khung hÃ¬nh: {body.aspect_ratio}
- Thá»i lÆ°á»£ng: {duration} giÃ¢y
- Hiá»‡u á»©ng hÃ¬nh: {body.transition}
- Phong cÃ¡ch ná»™i dung: {style_desc}
- Giá»ng vÄƒn: {tone_desc}
- Äá»™ dÃ i cÃ¢u trung bÃ¬nh: {sentence_range[0]}-{sentence_range[1]} tá»«
- Má»©c Ä‘á»™ chi tiáº¿t: {detail_desc}
{custom_instruction_text}

QUY Táº®C NGÃ”N NGá»® TUYá»†T Äá»I: má»i chuá»—i trong cáº£ keywords, visual_brief vÃ 
narration_lines pháº£i viáº¿t duy nháº¥t báº±ng {language_name}. NgÃ´n ngá»¯ cá»§a chá»§ Ä‘á»
Ä‘áº§u vÃ o khÃ´ng Ä‘Æ°á»£c lÃ m thay Ä‘á»•i ngÃ´n ngá»¯ Ä‘áº§u ra. KhÃ´ng xen tiáº¿ng Anh, trá»« tÃªn
riÃªng hoáº·c thuáº­t ngá»¯ khÃ´ng cÃ³ báº£n dá»‹ch tá»± nhiÃªn.

Tráº£ vá» Ä‘Ãºng JSON theo schema. YÃªu cáº§u:
1. keywords: 12-18 keyword/long-tail keyword sÃ¡t chá»§ Ä‘á», cÃ³ search intent, khÃ´ng nhá»“i tá»« khÃ³a, khÃ´ng bá»‹a sá»‘ liá»‡u xu hÆ°á»›ng.
   Báº¯t buá»™c giá»¯ nguyÃªn nghÄ©a vÃ  loáº¡i cá»§a thá»±c thá»ƒ trong chá»§ Ä‘á». KhÃ´ng Ä‘Æ°á»£c tÃ¡ch má»™t cá»¥m danh tá»« riÃªng thÃ nh cÃ¡c tá»« khÃ³a rá»i gÃ¢y Ä‘a nghÄ©a, khÃ´ng tá»± Ä‘á»•i Ä‘á»™ng váº­t thÃ nh cÃ¢y, Ä‘á»“ váº­t, Ä‘á»‹a danh hoáº·c khÃ¡i niá»‡m khÃ¡c. VÃ­ dá»¥ chá»§ Ä‘á» "Ä‘áº·c Ä‘iá»ƒm vá» con lá»­ng máº­t" pháº£i dÃ¹ng cÃ¡c cá»¥m nhÆ° "Ä‘á»™ng váº­t lá»­ng máº­t", "Ä‘áº·c Ä‘iá»ƒm lá»­ng máº­t", tuyá»‡t Ä‘á»‘i khÃ´ng dÃ¹ng "cÃ¢y lá»­ng máº­t".
2. visual_brief: Ä‘Ãºng {scene_count} cáº£nh, má»—i pháº§n tá»­ pháº£i cÃ³ chá»§ thá»ƒ cá»¥ thá»ƒ + hÃ nh Ä‘á»™ng nhÃ¬n tháº¥y Ä‘Æ°á»£c vÃ  cÃ³ thá»ƒ tÃ¬m hoáº·c tÃ¡i táº¡o thÃ nh footage. Má»—i cáº£nh pháº£i liÃªn quan trá»±c tiáº¿p Ä‘áº¿n chá»§ Ä‘á»; trÃ¡nh mÃ´ táº£ trá»«u tÆ°á»£ng, chá»¯/UI/logo. KhÃ´ng Ä‘Æ°á»£c táº¡o má»™t pháº§n tá»­ chá»‰ nÃ³i vá» phong cÃ¡ch, mÃ u sáº¯c, gÃ³c mÃ¡y hoáº·c thá»ƒ loáº¡i phim.
3. narration_lines: ká»‹ch báº£n hook â†’ giÃ¡ trá»‹ chÃ­nh â†’ CTA; má»—i pháº§n tá»­ lÃ  má»™t cÃ¢u nÃ³i tá»± nhiÃªn vá»›i Ä‘á»™ dÃ i {sentence_range[0]}-{sentence_range[1]} tá»«. ToÃ n bá»™ ká»‹ch báº£n pháº£i cÃ³ {narration_word_min}-{narration_word_max} tá»« (má»¥c tiÃªu {narration_word_target} tá»«) Ä‘á»ƒ giá»ng Ä‘á»c tá»± nhiÃªn láº¥p Ä‘áº§y khoáº£ng {duration} giÃ¢y. KhÃ´ng viáº¿t ká»‹ch báº£n ngáº¯n rá»“i yÃªu cáº§u tÄƒng tá»‘c/giáº£m tá»‘c, khÃ´ng láº·p Ã½, khÃ´ng tuyÃªn bá»‘ thiáº¿u cÄƒn cá»©. Visual vÃ  lá»i thoáº¡i pháº£i cÃ¹ng má»™t máº¡ch ná»™i dung. Giá»ng vÄƒn pháº£i {tone_desc}.
QUY Táº®C NHáº¤T QUÃN THá»°C THá»‚: quy táº¯c giá»¯ nguyÃªn nghÄ©a vÃ  loáº¡i thá»±c thá»ƒ á»Ÿ má»¥c 1 Ã¡p dá»¥ng cho cáº£ visual_brief vÃ  narration_lines. Má»i cáº£nh vÃ  cÃ¢u thoáº¡i pháº£i nÃ³i Ä‘Ãºng chá»§ thá»ƒ trong chá»§ Ä‘á»; náº¿u chá»§ Ä‘á» nÃ³i "con lá»­ng máº­t" thÃ¬ Ä‘Ã³ luÃ´n lÃ  Ä‘á»™ng váº­t lá»­ng máº­t, khÃ´ng bao giá» lÃ  cÃ¢y, Ä‘á»“ váº­t hoáº·c má»™t ngÆ°á»i Ä‘ang thá»±c hiá»‡n chá»§ Ä‘á».
Chá»‰ xuáº¥t má»™t JSON object há»£p lá»‡ cÃ³ Ä‘Ãºng ba key: keywords, visual_brief, narration_lines. KhÃ´ng dÃ¹ng Markdown hay code fence.
"""
    prompt += f"""

RETENTION + SEO QUALITY BAR:
- The first narration line must be a sharp viewer-facing hook, not a greeting and not "in this video/today we".
- If the output language is Vietnamese, every Vietnamese sentence must use full Vietnamese diacritics. Never output ASCII-only Vietnamese such as "Neu ban", "Dung mua", "khong dau", or "hay luu video".
- The narration must be complete for {duration} seconds. Do not return a short outline; write enough spoken lines to reach {narration_word_min}-{narration_word_max} words.
- Keywords must cover direct search terms, beginner/how-to intent, mistakes/pain points, review/comparison intent, and short-video platform intent.
- Visual brief lines must read like searchable footage or AI-image prompts: visible subject, action, setting, and the reason that shot supports the matching narration beat.
- Avoid generic filler such as "intro scene", "nice cinematic shot", "show the topic", or abstract concepts that cannot be filmed.
- For AI-generated visuals, avoid unnecessary close-ups of hands, fingers, faces, teeth, or eyes. Prefer over-the-shoulder, product-on-table, screen workflow, object detail, and wide documentary shots unless a human close-up is required.
- The final narration line should give a natural CTA that fits the topic.
"""
    prompt += (
        f"\nTIMING CHECK: {duration} giây; mục tiêu {narration_word_target} từ; "
        f"khoảng hợp lệ {narration_word_min}-{narration_word_max} từ.\n"
    )
    return prompt, scene_count


def _parse_creator_ai_result(
    raw: str, scene_count: int, *, generator: str, model: str,
    target_language: str,
    grounded: bool = False, search_queries: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    data = json.loads(raw)
    keywords = [str(item).strip() for item in data.get("keywords", []) if str(item).strip()][:18]
    visuals = [str(item).strip() for item in data.get("visual_brief", []) if str(item).strip()][:scene_count]
    narration = [str(item).strip() for item in data.get("narration_lines", []) if str(item).strip()]
    if not keywords or not visuals or not narration:
        raise RuntimeError(f"{model} tráº£ vá» ná»™i dung khÃ´ng Ä‘áº§y Ä‘á»§")
    # Small local models occasionally ignore the requested language for one
    # JSON field. Translate every field as a final normalization pass so the
    # three editor boxes can never intentionally target different languages.
    normalized = _translate_texts([*keywords, *visuals, *narration], target_language)
    keyword_end = len(keywords)
    visual_end = keyword_end + len(visuals)
    keywords = normalized[:keyword_end]
    visuals = normalized[keyword_end:visual_end]
    narration = normalized[visual_end:]
    return {
        "keywords": keywords,
        "visual_brief": "\n".join(visuals), "script": "\n".join(visuals),
        "narration_script": "\n".join(narration),
        "generator": generator, "model": model, "warning": None,
        "language": target_language,
        "grounded": grounded, "search_queries": (search_queries or [])[:8],
    }


def _ollama_creator_suggestions(body: CreatorSuggestionBody) -> Dict[str, Any]:
    base_url = (_env_first("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    configured_model = _env_first("OLLAMA_MODEL")
    tags = requests.get(f"{base_url}/api/tags", timeout=3)
    tags.raise_for_status()
    names = [str(item.get("name", "")) for item in tags.json().get("models", []) if item.get("name")]
    if not names:
        raise RuntimeError("Ollama chÆ°a cÃ³ model; hÃ£y cháº¡y: ollama pull qwen3:8b")
    model = configured_model if configured_model in names else names[0]
    if configured_model and configured_model not in names:
        logger.warning(
            "Ollama model %s is not installed; using available model %s",
            configured_model, model,
        )
    prompt, scene_count = _creator_ai_prompt(body)
    response = requests.post(
        f"{base_url}/api/chat",
        timeout=max(180, min(900, int(body.duration_seconds or 30))),
        json={
            "model": model, "stream": False, "format": "json",
            "messages": [
                {"role": "system", "content": "Chá»‰ tráº£ vá» JSON há»£p lá»‡, khÃ´ng thÃªm giáº£i thÃ­ch."},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.7, "num_predict": 16384, "num_ctx": 32768},
        },
    )
    response.raise_for_status()
    raw = response.json().get("message", {}).get("content", "")
    return _parse_creator_ai_result(
        raw, scene_count, generator="ollama", model=model,
        target_language=body.target_language,
    )


def _openrouter_creator_suggestions(body: CreatorSuggestionBody) -> Dict[str, Any]:
    api_key = _env_first("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("ChÆ°a cáº¥u hÃ¬nh OPENROUTER_API_KEY")
    requested_model = _env_first("OPENROUTER_MODEL") or "openrouter/free"
    prompt, scene_count = _creator_ai_prompt(body)
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        timeout=max(120, min(600, int(body.duration_seconds or 30))),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": requested_model,
            "messages": [
                {"role": "system", "content": "Return only one valid JSON object with no markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 16384,
        },
    )
    response.raise_for_status()
    payload = response.json()
    raw = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    actual_model = str(payload.get("model") or requested_model)
    return _parse_creator_ai_result(
        raw, scene_count, generator="openrouter", model=actual_model,
        target_language=body.target_language,
    )


def _openai_creator_suggestions(body: CreatorSuggestionBody) -> Dict[str, Any]:
    """Generate script suggestions using OpenAI API (GPT-4, GPT-3.5, etc.)."""
    if not OPENAI_AVAILABLE:
        raise RuntimeError("OpenAI library chÆ°a Ä‘Æ°á»£c cÃ i Ä‘áº·t. Cháº¡y: pip install openai")
    
    api_key = _env_first("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("ChÆ°a cáº¥u hÃ¬nh OPENAI_API_KEY")
    
    configured_model = _env_first("OPENAI_MODEL") or "gpt-4o"
    prompt, scene_count = _creator_ai_prompt(body, require_search=False)
    
    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=configured_model,
            messages=[
                {"role": "system", "content": "Return only one valid JSON object with no markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=16384,
        )
        raw = response.choices[0].message.content or ""
        actual_model = response.model
        return _parse_creator_ai_result(
            raw, scene_count, generator="openai", model=actual_model,
            target_language=body.target_language,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI API error: {exc}")


def _creator_ai_suggestions(body: CreatorSuggestionBody) -> Dict[str, Any]:
    api_key = _env_first("GEMINI_API_KEY", "GOOGLE_AI_API_KEY")
    if not api_key:
        raise RuntimeError("ChÆ°a cáº¥u hÃ¬nh GEMINI_API_KEY")
    configured_model = _env_first("GEMINI_MODEL") or "gemini-3.1-flash-lite"
    prompt, scene_count = _creator_ai_prompt(body, require_search=True)
    request_payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384},
    }
    response = None
    model = configured_model
    attempted_errors: List[str] = []
    for candidate_model in _select_gemini_models(api_key, configured_model):
        model = candidate_model
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=request_payload,
            timeout=max(60, min(600, int(body.duration_seconds or 30))),
        )
        if response.ok:
            break
        detail = response.text.strip().replace("\n", " ")[:500]
        attempted_errors.append(f"{model}: HTTP {response.status_code} {detail}")
        if response.status_code not in (404, 429):
            break
    if response is None or not response.ok:
        raise RuntimeError("; ".join(attempted_errors) or "KhÃ´ng gá»i Ä‘Æ°á»£c Gemini API")
    payload = response.json()
    candidate = payload.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    raw = "".join(part.get("text", "") for part in parts).strip()
    grounding = candidate.get("groundingMetadata") or {}
    search_queries = grounding.get("webSearchQueries") or []
    if not search_queries:
        raise RuntimeError("Gemini khÃ´ng thá»±c hiá»‡n Google Search grounding")
    return _parse_creator_ai_result(
        raw, scene_count, generator="gemini", model=model,
        target_language=body.target_language,
        grounded=True, search_queries=search_queries,
    )


def _creator_keywords_from_topic(
    topic: str, language: str, duration_seconds: Optional[int] = None,
) -> List[str]:
    subject, _ = _creator_topic_subject(topic, language)
    if language == "vi":
        base = [
            subject, f"Ä‘áº·c Ä‘iá»ƒm {subject}", f"sá»± tháº­t vá» {subject}",
            f"Ä‘iá»u Ã­t biáº¿t vá» {subject}", f"táº­p tÃ­nh {subject}",
            f"{subject} trong tá»± nhiÃªn", f"khÃ¡m phÃ¡ {subject}",
        ]
    else:
        base = [subject, f"facts about {subject}", f"{subject} characteristics", f"learn about {subject}"]
    result: List[str] = []
    for keyword in base:
        keyword = keyword.strip().lower()[:100]
        if keyword and keyword not in result:
            result.append(keyword)
    return result[:12]


def _creator_topic_subject(topic: str, language: str) -> tuple[str, str]:
    """Keep a topic's compound subject intact and retain its entity type."""
    text = " ".join(re.sub(r"[^\w\s]", " ", topic.lower(), flags=re.UNICODE).split())
    folded = _ascii_fold(text)
    match = re.search(r"\b(?:ve|cua)\s+(.+)$", folded)
    if match:
        prefix_words = len(folded[:match.start(1)].split())
        raw_subject = " ".join(text.split()[prefix_words:])
    else:
        raw_subject = text
    raw_subject = raw_subject.strip()
    raw_folded = _ascii_fold(raw_subject)
    animal = bool(re.match(r"^(?:con|loai)\s+", raw_folded))
    if animal:
        raw_subject = re.sub(r"^\S+\s+", "", raw_subject).strip()
    else:
        if re.match(r"^(?:cai|chiec|mot)\s+", raw_folded):
            raw_subject = re.sub(r"^\S+\s+", "", raw_subject).strip()
    raw_subject = raw_subject[:80].strip() or text[:80].strip()
    if animal:
        return (f"động vật {raw_subject}" if language == "vi" else raw_subject), "animal"
    return raw_subject, "unknown"


def _enforce_creator_entity_consistency(
    result: Dict[str, Any], topic: str, language: str,
) -> Dict[str, Any]:
    """Correct an explicit entity-type contradiction returned by a model."""
    subject, entity_kind = _creator_topic_subject(topic, language)
    if entity_kind != "animal" or language != "vi":
        return result
    animal_name = re.sub(r"^động vật\s+", "", subject, flags=re.IGNORECASE)
    wrong = re.compile(rf"\bcây\s+{re.escape(animal_name)}\b", flags=re.IGNORECASE)

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            return wrong.sub(subject, value)
        if isinstance(value, list):
            return [fix(item) for item in value]
        return value

    return {key: fix(value) for key, value in result.items()}


def _annotate_creator_suggestion_timing(
    result: Dict[str, Any], duration_seconds: int,
) -> Dict[str, Any]:
    annotated = dict(result)
    narration = str(annotated.get("narration_script") or "")
    visual = str(annotated.get("visual_brief") or annotated.get("script") or "")
    annotated["duration_seconds"] = int(duration_seconds)
    annotated["narration_word_count"] = len(narration.split())
    annotated["scene_count"] = len([line for line in visual.splitlines() if line.strip()])
    return annotated


def _validate_creator_suggestion_timing(
    result: Dict[str, Any], duration_seconds: int,
) -> Dict[str, Any]:
    """Reject an AI response that ignored the selected content duration."""
    duration = max(10, min(1200, int(duration_seconds or 30)))
    expected_scenes = max(4, min(150, round(duration / (5 if duration <= 60 else 8))))
    visual = str(result.get("visual_brief") or result.get("script") or "")
    scene_count = len([line for line in visual.splitlines() if line.strip()])
    narration = str(result.get("narration_script") or "")
    word_count = len(narration.split())
    words_per_second = 2.35 if result.get("language", "vi") == "vi" else 2.25
    target_words = max(25, round(duration * words_per_second))
    minimum_words = round(target_words * 0.90)
    maximum_words = round(target_words * 1.10)
    if scene_count != expected_scenes:
        raise RuntimeError(
            f"AI ignored duration: expected {expected_scenes} scenes, received {scene_count}"
        )
    if not minimum_words <= word_count <= maximum_words:
        raise RuntimeError(
            f"AI ignored duration: expected {minimum_words}-{maximum_words} narration words, received {word_count}"
        )
    return result


def _ascii_topic(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower().replace("Ä‘", "d"))
    return " ".join(re.findall(r"[a-z0-9]+", normalized.encode("ascii", "ignore").decode("ascii")))


def _creator_stock_profile(topic: str) -> Dict[str, Any]:
    normalized = _ascii_topic(topic)
    profiles = [
        ("beauty", ("lam dep", "my pham", "trang diem", "cham soc da", "skincare", "makeup", "beauty", "cosmetic", "spa"), [
            "natural beauty woman portrait soft light", "woman applying face serum skincare routine",
            "skincare cosmetics products flat lay", "makeup artist applying cosmetics beauty salon",
            "woman washing face morning skincare", "healthy glowing skin natural makeup close up",
            "luxury spa beauty treatment relaxing", "lipstick foundation mascara makeup products",
            "woman looking mirror beauty routine", "confident radiant woman beauty portrait",
        ]),
        ("food", ("nau an", "mon an", "am thuc", "food", "recipe", "cooking"), [
            "chef preparing fresh ingredients", "close up cooking healthy meal", "beautiful food plating restaurant",
        ]),
        ("fitness", ("tap luyen", "the hinh", "giam can", "yoga", "fitness", "workout", "gym"), [
            "fitness workout gym training", "healthy woman exercising", "yoga stretching wellness",
        ]),
        ("travel", ("du lich", "kham pha", "travel", "tourism", "hotel"), [
            "traveler exploring beautiful destination", "travel landscape adventure", "tourist enjoying vacation",
        ]),
        ("finance", ("tai chinh", "dau tu", "tiet kiem", "kinh doanh", "finance", "investment", "money"), [
            "personal finance planning money", "business investment charts", "saving money financial goals",
        ]),
        ("technology", ("cong nghe", "phan mem", "tri tue nhan tao", "ai", "technology", "software", "computer"), [
            "modern technology artificial intelligence", "professional using laptop software", "digital innovation interface",
        ]),
        ("education", ("hoc tap", "giao duc", "sinh vien", "education", "study", "learning"), [
            "student focused studying", "online learning laptop", "books notes education desk",
        ]),
    ]
    for category, markers, queries in profiles:
        if any(re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", normalized) for marker in markers):
            return {"category": category, "queries": queries, "topic": normalized}
    # Unknown topics remain topic-led. Never silently replace them with
    # unrelated office/AI footage.
    return {"category": "general", "queries": [normalized or "lifestyle"], "topic": normalized}


def _translate_texts(values: List[str], destination: str) -> List[str]:
    """Normalize a list of strings to one requested output language."""
    cleaned = [" ".join(value.split()) for value in values]
    if not cleaned:
        return []
    try:
        from googletrans import Translator as GoogleTranslator
        translator = GoogleTranslator()
        translated = translator.translate(cleaned, src="auto", dest=destination)
        # googletrans 3.x returns immediately, while newer 4.x builds return
        # a coroutine. This function runs inside the creator worker thread,
        # so asyncio.run is safe here and supports both package families.
        if inspect.isawaitable(translated):
            translated = asyncio.run(translated)
        if not isinstance(translated, list):
            translated = [translated]
        results = [" ".join(item.text.split()) for item in translated]
        if len(results) == len(cleaned) and all(results):
            return results
    except Exception:
        logger.exception("Could not normalize generated content to language=%s", destination)
    return cleaned


def _translate_stock_texts_to_english(values: List[str]) -> List[str]:
    """Translate arbitrary user topics/scenes for stock providers dynamically."""
    translated = _translate_texts(values, "en")
    return [value or _ascii_topic(original) or "lifestyle" for value, original in zip(translated, values)]


_STOCK_QUERY_STOPWORDS = {
    "a", "an", "the", "there", "is", "are", "was", "were", "this", "that",
    "with", "and", "or", "of", "to", "from", "in", "on", "at", "for", "into",
    "scene", "shot", "footage", "video", "showing", "shows", "appears", "suddenly",
    "one", "some", "very", "then", "next", "about", "related", "directly",
}


def _compact_stock_terms(text: str, limit: int = 7) -> str:
    """Keep concrete English search terms instead of sending prose to stock APIs."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    result: List[str] = []
    for word in words:
        if word in _STOCK_QUERY_STOPWORDS or len(word) < 2 or word in result:
            continue
        result.append(word)
        if len(result) >= limit:
            break
    return " ".join(result)


def _disambiguate_stock_terms(source_text: str, terms: str) -> str:
    """Resolve a few high-impact stock-search ambiguities from nearby verbs."""
    source_words = set(re.findall(r"[a-z0-9]+", source_text.lower()))
    if "mouse" in source_words:
        if source_words.intersection({"run", "runs", "running", "emerge", "emerges", "appears", "escape"}):
            return _compact_stock_terms(f"animal rodent {terms}", 7)
        if source_words.intersection({"click", "clicking", "computer", "laptop", "cursor"}):
            return _compact_stock_terms(f"computer mouse {terms}", 7)
    return terms


def _product_kind_stock_terms(text: str) -> tuple[str, str]:
    folded = _ascii_fold(text)
    if any(token in folded for token in ("hut bui", "vacuum", "cleaner")):
        return "handheld vacuum", "cleaning desk crumbs keyboard"
    if any(token in folded for token in ("den livestream", "ring light", "livestream light", "studio light")):
        return "ring light", "creator filming video at desk"
    if any(token in folded for token in ("may say", "hair dryer", "dryer")):
        return "hair dryer", "woman drying hair bathroom"
    if any(token in folded for token in ("serum", "skincare", "kem duong", "my pham", "cosmetic")):
        return "skincare product", "woman skincare routine bathroom mirror"
    if any(token in folded for token in ("tai nghe", "earbuds", "headphones")):
        return "wireless earbuds", "person using earbuds working desk"
    if any(token in folded for token in ("ban phim", "keyboard")):
        return "keyboard", "desk setup typing keyboard"
    return "product", "creator product review at home"


def _product_ad_stock_queries(
    translated_topic: str, translated_visual: str, translated_narration: str,
) -> List[str]:
    text = f"{translated_topic} {translated_visual} {translated_narration}"
    folded = _ascii_fold(text)
    if not any(marker in folded for marker in ("product ad", "model problem", "model use", "demo proof", "objection shot", "lifestyle shot")):
        return []
    product_term, action_term = _product_kind_stock_terms(text)
    if "model problem" in folded or "problem shot" in folded:
        if product_term == "handheld vacuum":
            queries = [
                "messy desk crumbs keyboard",
                "woman frustrated messy desk",
                "home office desk cleaning problem",
            ]
        else:
            queries = [
                f"person problem before using {product_term}",
                "creator frustrated at home desk",
                "home lifestyle problem scene",
            ]
    elif "model use" in folded or "demo proof" in folded:
        queries = [
            f"woman {action_term}",
            f"person using {product_term}",
            f"creator demonstrating {product_term}",
        ]
    elif "objection" in folded:
        queries = [
            f"person checking {product_term} details",
            "customer comparing product reviews phone",
            "person reading product reviews",
        ]
    elif "lifestyle" in folded:
        queries = [
            f"{action_term} lifestyle",
            f"creator using {product_term} at home",
            "natural ugc creator home",
        ]
    else:
        queries = [
            f"creator product review {product_term}",
            f"person using {product_term}",
            action_term,
        ]
    return [_compact_stock_terms(query, 8) for query in queries if _compact_stock_terms(query, 8)]


def _creator_stock_queries(
    translated_topic: str, translated_visual: str, translated_narration: str,
) -> List[str]:
    """Build short, specificity-first queries for one timeline scene.

    Voice content wins because it represents what the viewer hears at that
    exact moment. Visual brief is the second choice, while the broad topic is
    only a last resort and cannot drown out concrete subjects/actions.
    """
    product_ad_queries = _product_ad_stock_queries(
        translated_topic, translated_visual, translated_narration,
    )
    if product_ad_queries:
        return list(dict.fromkeys(product_ad_queries))
    directed = direct_visual_scene(translated_topic, translated_visual, translated_narration)
    narration = _disambiguate_stock_terms(
        translated_narration, _compact_stock_terms(translated_narration, 6),
    )
    visual = _compact_stock_terms(translated_visual, 7)
    topic = _compact_stock_terms(translated_topic, 5)
    candidates = [*directed.stock_queries, narration, visual]
    if narration and visual:
        candidates.insert(1, _compact_stock_terms(f"{narration} {visual}", 8))
    candidates.append(topic)
    return list(dict.fromkeys(query for query in candidates if query))


def _creator_image_story_prompts(
    topic: str, visuals: List[str], narrations: List[str],
) -> tuple[str, List[str]]:
    """Plan a coherent image sequence before invoking the diffusion model."""
    scene_count = len(visuals)
    aligned_narrations = [
        narrations[min(len(narrations) - 1, int(i * len(narrations) / max(1, scene_count)))]
        for i in range(scene_count)
    ]
    fallback_bible = (
        f"One coherent live-action documentary about {topic}; same recurring subjects, "
        "same appearance and location logic, realistic anatomy, natural light, real camera footage"
    )
    fallback_prompts = [
        f"{direct_visual_scene(topic, visual, aligned_narrations[i]).ai_prompt} Continuity: {fallback_bible}"
        for i, visual in enumerate(visuals)
    ]
    if scene_count > 24:
        logger.info("Long-form storyboard has %s scenes; using deterministic continuity prompts", scene_count)
        return fallback_bible, fallback_prompts
    base_url = (_env_first("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    try:
        health = requests.get(f"{base_url}/api/tags", timeout=1.5)
        if not health.ok:
            logger.info("Ollama storyboard is unavailable (HTTP %s); using deterministic prompts", health.status_code)
            return fallback_bible, fallback_prompts
    except requests.RequestException:
        # Ollama is an optional storyboard enhancer. A stopped local service
        # is normal and must not emit a full traceback or fail creator jobs.
        logger.info("Ollama storyboard is not running; using deterministic prompts")
        return fallback_bible, fallback_prompts
    try:
        model = _env_first("OLLAMA_MODEL") or "llama3:latest"
        scene_lines = "\n".join(
            f"Scene {i + 1}: visual={visual} | narration={aligned_narrations[i]}"
            for i, visual in enumerate(visuals)
        )
        prompt = f"""You are a film storyboard director. Turn the following complete short-video story into a visually coherent image sequence.
Topic: {topic}
{scene_lines}

Return JSON with exactly two keys:
- story_bible: one English string, maximum 45 words. Lock recurring character/species appearance, clothing, location logic, lighting and color palette for the whole story. The style MUST be live-action documentary photography with real anatomy and real-world textures, never animation, illustration, 3D render, CGI or cartoon.
- scene_prompts: exactly {scene_count} English strings, each maximum 55 words.

Every scene prompt MUST begin with the exact concrete subject and action from that scene. Preserve species and objects literally: never replace an animal/object with a person. Then repeat the relevant fixed appearance and style from story_bible. Scenes must progress chronologically as one story, not look like unrelated illustrations. Do not request diagrams, text, labels, UI, logos or split screens.
Only output valid JSON."""
        storyboard_timeout = max(15, min(180, int(_env_first("OLLAMA_STORYBOARD_TIMEOUT") or "60")))
        response = requests.post(
            f"{base_url}/api/chat", timeout=storyboard_timeout,
            json={
                "model": model, "stream": False, "format": "json",
                "messages": [
                    {"role": "system", "content": "Return only valid JSON. Preserve every concrete subject and action exactly."},
                    {"role": "user", "content": prompt},
                ],
                "options": {"temperature": 0.25, "num_predict": 600, "num_ctx": 4096},
            },
        )
        response.raise_for_status()
        raw = response.json().get("message", {}).get("content", "")
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE))
        story_bible = " ".join(str(data.get("story_bible", "")).split())
        prompts = [" ".join(str(item).split()) for item in data.get("scene_prompts", [])]
        if not story_bible or len(prompts) != scene_count or not all(prompts):
            raise RuntimeError("Storyboard AI returned the wrong number of scene prompts")
        # Put the exact user scene first so a verbose style bible cannot
        # push the important subject/action past SD-Turbo's token window.
        final_prompts = []
        bible_short = " ".join(story_bible.split()[:20])
        for i, visual in enumerate(visuals):
            visual_short = " ".join(visual.split()[:18])
            director_short = " ".join(prompts[i].split()[:22])
            matched_prompt = direct_visual_scene(topic, visual, aligned_narrations[i]).ai_prompt
            final_prompts.append(
                f"{visual_short}. {matched_prompt}. Continuity: {bible_short}. Scene direction: {director_short}"
            )
        return story_bible, final_prompts
    except requests.Timeout:
        logger.warning("Ollama storyboard timed out; using deterministic continuity prompts")
        return fallback_bible, fallback_prompts
    except requests.RequestException as exc:
        logger.warning("Ollama storyboard request failed (%s); using deterministic prompts", exc)
        return fallback_bible, fallback_prompts
    except Exception:
        logger.exception("Could not create AI storyboard; using deterministic continuity prompts")
        return fallback_bible, fallback_prompts


def _creator_video_backend() -> str:
    """Choose LTX on large GPUs and isolated low-memory SVD otherwise."""
    configured = (_env_first("CREATOR_VIDEO_BACKEND") or "auto").lower()
    if configured not in ("auto", "svd", "ltx"):
        raise HTTPException(503, "CREATOR_VIDEO_BACKEND chá»‰ nháº­n auto, svd hoáº·c ltx")
    if configured != "auto":
        return configured
    import torch
    if not torch.cuda.is_available():
        return "svd"
    total_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    ltx_min_vram_gb = max(8.0, float(_env_first("LTX_VIDEO_MIN_VRAM_GB") or "16"))
    return "ltx" if total_vram_gb >= ltx_min_vram_gb else "svd"


def _remote_creator_image_available() -> bool:
    if _env_first("HUGGINGFACE_API_KEY"):
        return True
    return bool(
        _env_first("USE_DALLE_IMAGES", "false").lower() == "true"
        and OPENAI_AVAILABLE
        and _env_first("OPENAI_API_KEY")
    )


def _local_creator_image_runtime_available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
        import transformers  # noqa: F401
        import accelerate  # noqa: F401
        import safetensors  # noqa: F401
        from diffusers import StableDiffusionPipeline  # noqa: F401
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _validate_creator_ai_runtime(image_provider: str) -> None:
    """Fail before creating/charging a job when its AI runtime is incomplete."""
    if image_provider not in ("ai", "cpu_ai", "ai_video"):
        return
    if image_provider in ("ai", "cpu_ai") and _remote_creator_image_available():
        return
    try:
        import torch
    except Exception as exc:
        raise HTTPException(
            503,
            "Môi trường AI ảnh/video đang lỗi khi import PyTorch/Numpy "
            f"({type(exc).__name__}: {exc}). Hãy sửa/cài lại numpy/torch trong .venv, "
            "hoặc chọn Stock/Product media thay vì AI Image.",
        ) from exc
    try:
        import diffusers
        import transformers
        import accelerate
        import safetensors
        from diffusers import StableDiffusionPipeline
    except (ImportError, RuntimeError, RecursionError) as exc:
        raise HTTPException(
            503,
            "Môi trường AI ảnh chưa đầy đủ hoặc không tương thích "
            f"({type(exc).__name__}: {exc}). Chạy: python -m pip install -r requirements.txt, "
            "sau đó khởi động lại backend.",
        ) from exc
    if image_provider == "ai_video":
        if not torch.cuda.is_available():
            cuda_build = getattr(torch.version, "cuda", None)
            if not cuda_build:
                raise HTTPException(
                    503,
                    f"Đã phát hiện PyTorch {torch.__version__} bản CPU-only. GPU NVIDIA có thể vẫn hoạt động, "
                    "nhưng cần cài lại PyTorch bản CUDA rồi khởi động lại web.",
                )
            raise HTTPException(
                503,
                f"PyTorch CUDA {cuda_build} chưa truy cập được GPU. Hãy kiểm tra driver NVIDIA rồi khởi động lại web.",
            )
        backend = _creator_video_backend()
        try:
            if backend == "ltx":
                from diffusers import LTXImageToVideoPipeline
            else:
                from diffusers import StableVideoDiffusionPipeline
        except (ImportError, RuntimeError, RecursionError) as exc:
            raise HTTPException(503, f"Diffusers không hỗ trợ backend video {backend.upper()}: {exc}") from exc


def _split_creator_script(topic: str, script: Optional[str], language: str) -> List[str]:
    raw = (script or "").strip()
    if not raw:
        return _creator_scene_brief_from_topic(topic, language)
    lines = [line.strip(" -\t") for line in raw.splitlines() if line.strip()]
    if len(lines) <= 1:
        lines = [s.strip() for s in re.split(r"(?<=[.!?ã€‚ï¼ï¼Ÿ])\s+", raw) if s.strip()]
    if len(lines) > 1:
        style_only_prefixes = (
            "phong cÃ¡ch", "mÃ u sáº¯c", "tÃ´ng mÃ u", "gÃ³c mÃ¡y", "thá»ƒ loáº¡i",
            "style", "color palette", "colour palette", "camera style", "genre",
        )
        concrete_lines = [
            line for line in lines
            if not line.lower().lstrip().startswith(style_only_prefixes)
        ]
        if concrete_lines:
            lines = concrete_lines
    return lines[:150] or _creator_scene_brief_from_topic(topic, language)


def _wrap_creator_text(text: str, max_chars: int = 28) -> str:
    words = text.split()
    if not words:
        return text
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines[:5])


def _stock_orientation(aspect_ratio: str) -> str:
    return "landscape" if aspect_ratio == "16:9" else "portrait"


def _pick_pexels_video_file(video: Dict[str, Any]) -> Optional[str]:
    files = video.get("video_files") or []
    mp4s = [f for f in files if f.get("file_type") == "video/mp4" and f.get("link")]
    if not mp4s:
        return None
    mp4s.sort(key=lambda f: abs((f.get("width") or 720) - 1080) + abs((f.get("height") or 1280) - 1920))
    return mp4s[0]["link"]


def _search_stock_media(query: str, aspect_ratio: str) -> Optional[Dict[str, str]]:
    orientation = _stock_orientation(aspect_ratio)
    pexels_key = _env_first("PEXELS_API_KEY", "PEXELS_KEY")
    pixabay_key = _env_first("PIXABAY_API_KEY", "PIXABAY_KEY")

    if pexels_key:
        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": pexels_key},
                params={"query": query, "per_page": 3, "orientation": orientation},
                timeout=15,
            )
            resp.raise_for_status()
            for video in resp.json().get("videos", []):
                url = _pick_pexels_video_file(video)
                if url:
                    return {"type": "video", "url": url, "provider": "Pexels"}
        except Exception:
            logger.exception("Pexels video search failed for query=%s", query)

        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": pexels_key},
                params={"query": query, "per_page": 3, "orientation": orientation},
                timeout=15,
            )
            resp.raise_for_status()
            for photo in resp.json().get("photos", []):
                src = photo.get("src") or {}
                url = src.get("large2x") or src.get("large") or src.get("original")
                if url:
                    return {"type": "image", "url": url, "provider": "Pexels"}
        except Exception:
            logger.exception("Pexels photo search failed for query=%s", query)

    if pixabay_key:
        try:
            resp = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": pixabay_key, "q": query, "per_page": 3,
                    "orientation": "horizontal" if aspect_ratio == "16:9" else "vertical",
                },
                timeout=15,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                videos = hit.get("videos") or {}
                item = videos.get("large") or videos.get("medium") or videos.get("small") or {}
                if item.get("url"):
                    return {"type": "video", "url": item["url"], "provider": "Pixabay"}
        except Exception:
            logger.exception("Pixabay video search failed for query=%s", query)

        try:
            resp = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": pixabay_key, "q": query, "per_page": 3,
                    "orientation": "horizontal" if aspect_ratio == "16:9" else "vertical",
                    "image_type": "photo",
                },
                timeout=15,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                url = hit.get("largeImageURL") or hit.get("webformatURL")
                if url:
                    return {"type": "image", "url": url, "provider": "Pixabay"}
        except Exception:
            logger.exception("Pixabay image search failed for query=%s", query)

    return None


def _download_stock_media(media: Dict[str, str], dest: Path) -> Path:
    suffix = ".mp4" if media["type"] == "video" else ".jpg"
    output = dest.with_suffix(suffix)
    with requests.get(
        media["url"], stream=True, timeout=(15, 120),
        headers={"User-Agent": "Mozilla/5.0 VideoLocalizationAI/1.0"},
    ) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            raise RuntimeError("NhÃ  cung cáº¥p media tráº£ vá» HTML thay vÃ¬ áº£nh/video")
        with output.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    return output


def _creator_image_negative_prompt() -> str:
    return (
        "deformed anatomy, disfigured face, misaligned face, detached head, extra fingers, missing fingers, "
        "fused fingers, twisted hands, broken wrists, duplicated hands, malformed hands, long fingers, "
        "crossed eyes, asymmetric eyes, warped body, disconnected limbs, floating limbs, bad proportions, "
        "cartoon, anime, illustration, CGI, 3D render, text, logo, watermark, blurry, low quality"
    )


def _harden_creator_image_prompt(prompt: str, max_words: int = 120) -> str:
    scene = " ".join(str(prompt or "").split())
    scene = " ".join(scene.split()[:max_words])
    return (
        f"{scene}. Live-action documentary photography, realistic real-world scene, stable subject identity, "
        "anatomically correct body proportions, face attached naturally to the body, natural hands only if hands are required, "
        "avoid close-up fingers, avoid distorted faces, prefer over-the-shoulder or medium shot for people, "
        "clear subject-action-background match, natural light, no text, no watermark, no logo."
    )


def _generate_huggingface_image(
    prompt: str, output_path: Path, aspect_ratio: str,
) -> Path:
    """Generate image using Hugging Face Inference API (free)."""
    hf_api_key = _env_first("HUGGINGFACE_API_KEY")
    if not hf_api_key:
        raise RuntimeError("HUGGINGFACE_API_KEY not configured in .env file")
    
    image_prompt = _harden_creator_image_prompt(prompt, max_words=120)
    negative_prompt = _creator_image_negative_prompt()
    
    # Use Stable Diffusion XL for better quality
    model_id = _env_first("HF_IMAGE_MODEL") or "stabilityai/stable-diffusion-xl-base-1.0"
    
    # Determine dimensions based on aspect ratio
    if aspect_ratio == "16:9":
        width, height = 1024, 576
    elif aspect_ratio == "9:16":
        width, height = 576, 1024
    else:
        width, height = 768, 768
    
    try:
        import requests
        
        api_url = f"https://api-inference.huggingface.co/models/{model_id}"
        headers = {
            "Authorization": f"Bearer {hf_api_key}",
        }
        payload = {
            "inputs": image_prompt,
            "parameters": {
                "width": width,
                "height": height,
                "num_inference_steps": 30,
                "guidance_scale": 7.5,
                "negative_prompt": negative_prompt,
            },
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        # Save image
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(response.content)
        
        logger.info(f"Generated image using Hugging Face: {image_prompt[:180]}")
        return output_path
        
    except Exception as exc:
        logger.warning(f"Failed to generate image with Hugging Face: {exc}")
        raise RuntimeError(f"KhÃ´ng thá»ƒ sinh áº£nh vá»›i Hugging Face: {exc}") from exc


def _generate_dalle_image(
    prompt: str, output_path: Path, aspect_ratio: str,
) -> Path:
    """Generate image using DALL-E API for better quality."""
    if not OPENAI_AVAILABLE:
        raise RuntimeError("OpenAI library not available")
    
    openai_api_key = _env_first("OPENAI_API_KEY")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not configured in .env file")
    
    image_prompt = _harden_creator_image_prompt(prompt, max_words=220)
    
    # Determine size based on aspect ratio
    size = "1024x1024"  # Default square
    if aspect_ratio == "16:9":
        size = "1792x1024"  # Landscape
    elif aspect_ratio == "9:16":
        size = "1024x1792"  # Portrait
    
    try:
        client = openai.OpenAI(api_key=openai_api_key)
        response = client.images.generate(
            model="dall-e-3",
            prompt=image_prompt,
            size=size,
            quality="standard",
            n=1,
        )
        
        image_url = response.data[0].url
        
        # Download image
        img_response = requests.get(image_url, timeout=30)
        img_response.raise_for_status()
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_response.content)
        
        logger.info(f"Generated image using DALL-E: {image_prompt[:180]}")
        return output_path
        
    except Exception as exc:
        logger.warning(f"Failed to generate image with DALL-E: {exc}")
        raise RuntimeError(f"KhÃ´ng thá»ƒ sinh áº£nh vá»›i DALL-E: {exc}") from exc


def _generate_ai_image(
    prompt: str, output_path: Path, aspect_ratio: str, story_seed: Optional[int] = None,
    for_video: bool = False,
) -> Path:
    """Generate a scene image locally, automatically using CUDA when available."""
    import re
    global _IMAGE_AI_PIPELINE, _IMAGE_AI_DEVICE
    # Diffusers exposes several lazy modules; importing AutoPipeline from
    # two creator worker threads at the same time can recurse through its
    # optional PEFT/ControlNet imports. Serialize the import and use the
    # concrete SD pipeline required by sd-turbo instead.
    with _IMAGE_AI_LOCK:
        try:
            import torch
            from diffusers import StableDiffusionPipeline
        except (ImportError, RuntimeError, RecursionError) as exc:
            raise RuntimeError(
                "KhÃ´ng khá»Ÿi táº¡o Ä‘Æ°á»£c thÆ° viá»‡n AI áº£nh; hÃ£y restart backend rá»“i thá»­ láº¡i"
            ) from exc

    model_id = _local_ai_image_model_id()
    requested_device = (_env_first("IMAGE_AI_DEVICE") or "auto").lower()
    if requested_device not in ("auto", "cpu", "cuda"):
        raise RuntimeError("IMAGE_AI_DEVICE chá»‰ nháº­n auto, cpu hoáº·c cuda")
    if requested_device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("ÄÃ£ chá»n CUDA nhÆ°ng PyTorch khÃ´ng nháº­n Ä‘Æ°á»£c GPU NVIDIA")
    device = "cuda" if (requested_device == "cuda" or (requested_device == "auto" and torch.cuda.is_available())) else "cpu"
    precision = (_env_first("IMAGE_AI_PRECISION") or "auto").lower()
    if precision not in ("auto", "fp16", "fp32"):
        raise RuntimeError("IMAGE_AI_PRECISION chá»‰ nháº­n auto, fp16 hoáº·c fp32")
    gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else ""
    # GTX 16-series cards can execute FP16 but SD-Turbo's VAE commonly
    # overflows to NaN on them, producing a successfully saved all-black
    # image. Default those cards to FP32; RTX cards retain the faster FP16.
    fp16_safe = device == "cuda" and not re.search(r"\bGTX\s*16\d{2}\b", gpu_name, re.IGNORECASE)
    use_fp16 = precision == "fp16" or (precision == "auto" and fp16_safe)
    dtype = torch.float16 if use_fp16 else torch.float32
    with _IMAGE_AI_LOCK:
        if _IMAGE_AI_PIPELINE is None or _IMAGE_AI_DEVICE != device:
            logger.info(
                "Loading image model %s on %s with %s (first run may download several GB)",
                model_id, device.upper(), str(dtype).removeprefix("torch."),
            )
            _IMAGE_AI_PIPELINE = StableDiffusionPipeline.from_pretrained(
                model_id, torch_dtype=dtype, use_safetensors=True,
            )
            _IMAGE_AI_PIPELINE.to(device)
            _IMAGE_AI_DEVICE = device
            if device == "cpu":
                _IMAGE_AI_PIPELINE.enable_attention_slicing()

        # Keep CPU inference practical. FFmpeg scales/crops this image to the
        # final 1080p canvas later.
        if for_video:
            width, height = ((768, 432) if aspect_ratio == "16:9" else (432, 768))
            steps = max(1, min(4, int(_env_first("IMAGE_AI_VIDEO_STEPS") or "2")))
        else:
            width, height = ((640, 384) if aspect_ratio == "16:9" else (384, 640))
            steps = max(1, min(4, int(_env_first("IMAGE_AI_STEPS", "CPU_IMAGE_STEPS") or "1")))
        # CLIP truncates after 77 tokens. Keep the concrete scene at the front
        # and leave enough room for the essential realism constraints.
        scene_prompt = _harden_creator_image_prompt(prompt, max_words=90)
        tokenizer = _IMAGE_AI_PIPELINE.tokenizer
        scene_ids = tokenizer(
            scene_prompt, add_special_tokens=False, truncation=True, max_length=42,
        )["input_ids"]
        scene_prompt = tokenizer.decode(scene_ids, skip_special_tokens=True).strip()
        full_prompt = (
            f"{scene_prompt}. professional photography, high quality, realistic, "
            "natural lighting, no text, no watermark, no logo"
        )
        # A final tokenizer-level clamp is exact; word slicing is not because
        # accented/compound words may consume several CLIP tokens.
        final_ids = tokenizer(
            full_prompt, add_special_tokens=False, truncation=True,
            max_length=max(1, tokenizer.model_max_length - 2),
        )["input_ids"]
        full_prompt = tokenizer.decode(final_ids, skip_special_tokens=True).strip()
        seed = story_seed if story_seed is not None else int.from_bytes(
            full_prompt.encode("utf-8")[:8].ljust(8, b"0"), "little",
        ) % (2**31)
        generator = torch.Generator(device=device).manual_seed(seed)
        guidance_scale = float(_env_first("IMAGE_AI_GUIDANCE_SCALE") or "1.0")
        result = _IMAGE_AI_PIPELINE(
            prompt=full_prompt, width=width, height=height,
            negative_prompt=_creator_image_negative_prompt(),
            num_inference_steps=steps, guidance_scale=guidance_scale, generator=generator,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.images[0].save(output_path, format="JPEG", quality=92)
    return output_path


def _release_image_ai_pipeline() -> None:
    """Free the storyboard image model before loading the much larger video model."""
    global _IMAGE_AI_PIPELINE, _IMAGE_AI_DEVICE
    with _IMAGE_AI_LOCK:
        _IMAGE_AI_PIPELINE = None
        _IMAGE_AI_DEVICE = None
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _generate_ai_video(
    image_path: Path,
    prompt: str,
    output_path: Path,
    aspect_ratio: str,
    duration: float,
    story_seed: int,
) -> Path:
    """Animate a storyboard keyframe with LTX-Video on an NVIDIA GPU."""
    global _VIDEO_AI_PIPELINE, _VIDEO_AI_MODEL
    try:
        import torch
        from PIL import Image
        from diffusers import LTXImageToVideoPipeline
        from diffusers.utils import export_to_video
    except (ImportError, RuntimeError, RecursionError) as exc:
        raise RuntimeError("KhÃ´ng khá»Ÿi táº¡o Ä‘Æ°á»£c LTX-Video; hÃ£y kiá»ƒm tra diffusers/transformers") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("AI Sinh Video cáº§n NVIDIA GPU/CUDA nhÆ°ng PyTorch hiá»‡n khÃ´ng nháº­n GPU")

    model_id = _env_first("LTX_VIDEO_MODEL") or "Lightricks/LTX-Video-0.9.5"
    # LTX requires dimensions divisible by 32 and frame counts in the form 8n+1.
    width, height = ((704, 416) if aspect_ratio == "16:9" else (416, 704))
    fps = max(8, min(30, int(_env_first("LTX_VIDEO_FPS") or "24")))
    requested_frames = min(
        max(25, int(round(min(duration, 4.0) * fps))),
        max(25, int(_env_first("LTX_VIDEO_MAX_FRAMES") or "49")),
    )
    num_frames = max(25, ((requested_frames - 1) // 8) * 8 + 1)
    steps = max(4, min(50, int(_env_first("LTX_VIDEO_STEPS") or "30")))
    use_offload = (_env_first("LTX_VIDEO_CPU_OFFLOAD") or "true").lower() not in ("0", "false", "no")
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

    with _VIDEO_AI_LOCK:
        if _VIDEO_AI_PIPELINE is None or _VIDEO_AI_MODEL != model_id:
            logger.info("Loading LTX video model %s on CUDA (first run downloads model weights)", model_id)
            _VIDEO_AI_PIPELINE = LTXImageToVideoPipeline.from_pretrained(
                model_id, torch_dtype=dtype,
            )
            _VIDEO_AI_PIPELINE.vae.enable_tiling()
            if use_offload:
                _VIDEO_AI_PIPELINE.enable_model_cpu_offload()
            else:
                _VIDEO_AI_PIPELINE.to("cuda")
            _VIDEO_AI_MODEL = model_id

        image = Image.open(image_path).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        negative_prompt = (
            f"{_creator_image_negative_prompt()}, identity change, flicker, jitter, unnatural motion, "
            "face drifting away from body, hands detached from arms"
        )
        generator = torch.Generator(device="cuda").manual_seed(story_seed)
        frames = _VIDEO_AI_PIPELINE(
            image=image,
            prompt=(
                f"{_harden_creator_image_prompt(prompt, max_words=90)}. Live-action cinematic documentary footage, natural realistic motion, "
                "physically accurate movement, stable subject identity, coherent camera movement, no scene cut."
            ),
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            num_frames=num_frames,
            frame_rate=fps,
            num_inference_steps=steps,
            guidance_scale=3.0,
            decode_timestep=0.05,
            decode_noise_scale=0.025,
            generator=generator,
        ).frames[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        export_to_video(frames, str(output_path), fps=fps)
    return output_path


def _generate_svd_video_isolated(
    image_path: Path,
    output_path: Path,
    aspect_ratio: str,
    duration: float,
    story_seed: int,
) -> Path:
    """Run low-VRAM SVD out-of-process so a CUDA/native crash cannot kill web."""
    worker = _REPO_ROOT / "scripts" / "svd_worker.py"
    timeout = max(180, int(_env_first("SVD_WORKER_TIMEOUT_SECONDS") or "1800"))
    command = [
        sys.executable, str(worker),
        "--input", str(image_path.resolve()),
        "--output", str(output_path.resolve()),
        "--aspect-ratio", aspect_ratio,
        "--duration", f"{duration:.3f}",
        "--seed", str(story_seed),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        command, cwd=str(_REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=creationflags,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "SVD worker stopped unexpectedly")[-5000:]
        raise RuntimeError(f"SVD worker exit={result.returncode}: {detail}")
    if not output_path.exists() or output_path.stat().st_size < 4096:
        raise RuntimeError("SVD worker khÃ´ng táº¡o Ä‘Æ°á»£c video há»£p lá»‡")
    logger.info("SVD worker completed: %s", (result.stdout or "").strip()[-1000:])
    return output_path


def _render_stock_clip(
    source_path: Path,
    source_type: str,
    output_path: Path,
    width: int,
    height: int,
    duration: float,
    animation: str = "fade",
) -> None:
    # Stock sources commonly mix 23.976/25/29.97 fps. xfade rejects mixed
    # or variable rates, so normalize every scene to a real CFR timeline.
    base_vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},fps=30,settb=AVTB,setpts=N/(30*TB),format=yuv420p"
    )
    effect = min(0.55, max(0.25, duration / 5))
    if animation in {"slideleft", "slideright"}:
        x_expr = (
            f"if(lt(t,{effect:.3f}),{width}*(1-t/{effect:.3f}),"
            f"if(gt(t,{duration - effect:.3f}),{width}*(t-{duration - effect:.3f})/{effect:.3f},0))"
            if animation == "slideleft" else
            f"if(lt(t,{effect:.3f}),{width}*t/{effect:.3f},"
            f"if(gt(t,{duration - effect:.3f}),{width}*(1-(t-{duration - effect:.3f})/{effect:.3f}),{width}))"
        )
        vf = (
            f"{base_vf},pad={width * 2}:{height}:{width if animation == 'slideright' else 0}:0:black,"
            f"crop={width}:{height}:x='{x_expr}':y=0"
        )
    elif animation in {"slideup", "slidedown"}:
        y_expr = (
            f"if(lt(t,{effect:.3f}),{height}*(1-t/{effect:.3f}),"
            f"if(gt(t,{duration - effect:.3f}),{height}*(t-{duration - effect:.3f})/{effect:.3f},0))"
            if animation == "slideup" else
            f"if(lt(t,{effect:.3f}),{height}*t/{effect:.3f},"
            f"if(gt(t,{duration - effect:.3f}),{height}*(1-(t-{duration - effect:.3f})/{effect:.3f}),{height}))"
        )
        vf = (
            f"{base_vf},pad={width}:{height * 2}:0:{height if animation == 'slidedown' else 0}:black,"
            f"crop={width}:{height}:x=0:y='{y_expr}'"
        )
    elif animation in {"zoomin", "zoomout"}:
        zoom = (
            "min(zoom+0.0015,1.12)" if animation == "zoomin"
            else "if(eq(on,1),1.12,max(1.0,zoom-0.0015))"
        )
        vf = (
            f"{base_vf},zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30"
        )
    elif animation != "none":
        vf = (
            f"{base_vf},fade=t=in:st=0:d={effect:.3f},"
            f"fade=t=out:st={max(0, duration - effect):.3f}:d={effect:.3f}"
        )
    else:
        vf = base_vf
    if source_type == "image":
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-t", f"{duration:.3f}",
            "-i", str(source_path), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", WEB_RENDER_PRESET, "-crf", str(WEB_RENDER_CRF),
            "-r", "30", "-fps_mode", "cfr",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-t", f"{duration:.3f}",
            "-i", str(source_path), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", WEB_RENDER_PRESET, "-crf", str(WEB_RENDER_CRF),
            "-r", "30", "-fps_mode", "cfr",
            str(output_path),
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "FFmpeg stock clip render failed")


def _render_product_ad_clip(
    source_path: Path,
    source_type: str,
    output_path: Path,
    width: int,
    height: int,
    duration: float,
    scene: str,
    index: int,
) -> None:
    """Render uploaded product media as an ad mockup, not a plain slideshow.

    This keeps the real product visible while adding a blurred brand-style
    background, foreground motion, and beat-specific composition. It is a
    deterministic replacement for unavailable image-to-image reference models.
    """
    folded = _ascii_fold(scene)
    fg_width = int(width * (0.74 if "detail shot" not in folded else 0.84))
    if "cta shot" in folded or "final frame" in folded:
        fg_width = int(width * 0.66)
    y_ratio = 0.16
    if "product reveal" in folded or "detail shot" in folded:
        y_ratio = 0.11
    elif "cta shot" in folded or "final frame" in folded:
        y_ratio = 0.2
    x_motion = "sin(t*1.4)*10"
    y_motion = "sin(t*1.8)*16"
    bg = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},boxblur=24:2,eq=brightness=-0.08:saturation=1.15,"
        "format=yuv420p"
    )
    fg = (
        f"scale={fg_width}:-2:force_original_aspect_ratio=decrease:flags=lanczos,"
        "format=rgba,"
        "pad=iw+48:ih+48:24:24:color=black@0,"
        "drawbox=x=8:y=8:w=iw-16:h=ih-16:color=white@0.22:t=6"
    )
    overlay = (
        f"overlay=x='(W-w)/2+{x_motion}':y='{height * y_ratio:.1f}+{y_motion}':"
        "format=auto"
    )
    grade = "eq=contrast=1.08:saturation=1.12,format=yuv420p"
    filter_complex = f"[0:v]split=2[rawbg][rawfg];[rawbg]{bg}[bg];[rawfg]{fg}[fg];[bg][fg]{overlay},{grade}[v]"
    if source_type == "image":
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-t", f"{duration:.3f}",
            "-i", str(source_path), "-filter_complex", filter_complex,
            "-map", "[v]", "-an", "-c:v", "libx264", "-preset", WEB_RENDER_PRESET,
            "-crf", str(WEB_RENDER_CRF), "-r", "30", "-fps_mode", "cfr",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-t", f"{duration:.3f}",
            "-i", str(source_path), "-filter_complex", filter_complex,
            "-map", "[v]", "-an", "-c:v", "libx264", "-preset", WEB_RENDER_PRESET,
            "-crf", str(WEB_RENDER_CRF), "-r", "30", "-fps_mode", "cfr",
            str(output_path),
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "FFmpeg product ad clip render failed")


CREATOR_TRANSITIONS = {
    "none", "fade", "slideleft", "slideright", "slideup", "slidedown", "zoomin", "zoomout",
}


def _render_creator_timeline(
    clip_paths: List[Path], output_path: Path, scene_duration: float,
    requested_duration: float, transition: str,
) -> bool:
    """Join clips with normalized timestamps, optionally using xfade."""
    inputs: List[str] = []
    for path in clip_paths:
        inputs.extend(["-i", str(path)])

    if transition == "none" or len(clip_paths) == 1:
        prepared = "".join(
            f"[{i}:v]framerate=fps=30,setpts=PTS-STARTPTS[v{i}];"
            for i in range(len(clip_paths))
        )
        joined = "".join(f"[v{i}]" for i in range(len(clip_paths)))
        graph = f"{prepared}{joined}concat=n={len(clip_paths)}:v=1:a=0[vout]"
        final_duration = scene_duration * len(clip_paths)
    else:
        effect_duration = min(0.7, max(0.3, scene_duration / 4))
        parts = [
            f"[{i}:v]framerate=fps=30,setpts=PTS-STARTPTS[v{i}]"
            for i in range(len(clip_paths))
        ]
        previous = "v0"
        for i in range(1, len(clip_paths)):
            raw_output = f"rawx{i}"
            output = f"x{i}"
            offset = i * scene_duration - i * effect_duration
            parts.append(
                f"[{previous}][v{i}]xfade=transition={transition}:duration={effect_duration:.3f}:"
                f"offset={offset:.3f}[{raw_output}]"
            )
            # FFmpeg 7 can drop the frame-rate metadata on an xfade output;
            # restore it before feeding that output into the next xfade.
            parts.append(f"[{raw_output}]framerate=fps=30[{output}]")
            previous = output
        parts.append(f"[{previous}]format=yuv420p[vout]")
        graph = ";".join(parts)
        final_duration = scene_duration * len(clip_paths) - effect_duration * (len(clip_paths) - 1)

    cmd = [
        "ffmpeg", "-y", *inputs, "-filter_complex", graph,
        "-map", "[vout]", "-t", f"{final_duration:.3f}", "-an",
        "-c:v", "libx264", "-preset", WEB_RENDER_PRESET, "-crf", str(WEB_RENDER_CRF),
        "-r", "30", "-fps_mode", "cfr",
        "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        if transition != "none":
            logger.warning(
                "Transition %s failed; completing video without transition: %s",
                transition, (result.stderr or result.stdout or "unknown FFmpeg error")[-2000:],
            )
            return _render_creator_timeline(
                clip_paths, output_path, scene_duration, requested_duration, "none",
            )
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg timeline render failed")[-4000:])
    return transition != "none"


def _srt_timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _ass_timestamp(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, centis = divmod(centis, 360_000)
    minutes, centis = divmod(centis, 6_000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_karaoke_text(
    text: str, duration: float, word_durations_cs: Optional[List[int]] = None,
) -> str:
    """ASS karaoke: unspoken words are white, spoken words turn yellow."""
    lines = text.split("\n")
    words = [word for line in lines for word in line.split()]
    total_chars = sum(max(1, len(word)) for word in words) or 1
    measured = word_durations_cs if word_durations_cs and len(word_durations_cs) == len(words) else None
    total_cs = max(len(words), round(duration * 100))
    remaining_cs = total_cs
    remaining_chars = total_chars
    rendered_lines: List[str] = []
    flat_word_index = 0
    for line_index, line in enumerate(lines):
        rendered_words: List[str] = []
        line_words = line.split()
        for word_index, word in enumerate(line_words):
            is_last = line_index == len(lines) - 1 and word_index == len(line_words) - 1
            weight = max(1, len(word))
            if measured:
                word_cs = max(1, measured[flat_word_index])
            else:
                word_cs = remaining_cs if is_last else max(1, round(remaining_cs * weight / remaining_chars))
            remaining_cs -= word_cs
            remaining_chars -= weight
            flat_word_index += 1
            safe_word = word.replace("{", "(").replace("}", ")").replace("\\", "")
            rendered_words.append(f"{{\\kf{word_cs}}}{safe_word}")
        rendered_lines.append(" ".join(rendered_words))
    return r"\N".join(rendered_lines)


def _fit_creator_subtitle_text(text: str, frame_width: int, font_size: int) -> str:
    """Hard-wrap ASS cues so subtitles stay inside the video frame."""
    clean = " ".join(str(text or "").replace("\n", " ").split())
    if not clean:
        return clean
    # Approximate DejaVu Sans bold width. Keep enough side margin for 9:16.
    max_chars = max(18, int((frame_width * 0.80) / (font_size * 0.58)))
    words = clean.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return "\n".join(lines)
    midpoint = max(1, len(words) // 2)
    best = min(
        range(max(1, midpoint - 4), min(len(words), midpoint + 5)),
        key=lambda idx: abs(len(" ".join(words[:idx])) - len(" ".join(words[idx:]))),
    )
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def _write_creator_subtitles(
    scenes: List[str], path: Path, spoken_duration: float,
    cue_durations: Optional[List[float]] = None, frame_width: int = 1080,
    frame_height: int = 1920, cue_word_durations: Optional[List[List[int]]] = None,
) -> tuple[List[Dict[str, Any]], float]:
    segments: List[Dict[str, Any]] = []
    dialogues: List[str] = []
    # Explicit PlayRes makes margins/font sizes deterministic on both 9:16
    # and 16:9 output. Creator subtitles are intentionally capped at two
    # lines; long cues are split by time before this point, then hard-wrapped
    # here so libass cannot overflow off-screen.
    font_size = 38 if frame_height >= frame_width else 40
    margin_lr = max(72, round(frame_width * 0.09))
    margin_v = max(90, round(frame_height * 0.075))
    cues_text = [
        _fit_creator_subtitle_text(chunk, frame_width, font_size)
        for scene in scenes
        for chunk in _balanced_caption_chunks(scene, max_chars=100, line_chars=52)
    ]
    weights = (
        [max(0.01, duration) for duration in cue_durations]
        if cue_durations and len(cue_durations) == len(cues_text)
        else [max(1, len(re.sub(r"\s+", "", cue))) for cue in cues_text]
    )
    total_weight = sum(weights) or 1
    cues: List[tuple[str, float, float]] = []
    elapsed_weight = 0
    for cue, weight in zip(cues_text, weights):
        start = spoken_duration * elapsed_weight / total_weight
        elapsed_weight += weight
        end = spoken_duration * elapsed_weight / total_weight
        cues.append((cue, start, end))
    for cue_index, (text, start, end) in enumerate(cues):
        segments.append({"start": start, "end": end, "text": text})
        measured_words = (
            cue_word_durations[cue_index]
            if cue_word_durations and cue_index < len(cue_word_durations) else None
        )
        # If the complete narration was compressed to fit the visual track,
        # apply the same scale to Edge's real word-boundary durations.
        cue_scale = (end - start) / weights[cue_index] if measured_words and weights[cue_index] else 1.0
        scaled_words = [max(1, round(cs * cue_scale)) for cs in measured_words] if measured_words else None
        karaoke = _ass_karaoke_text(text, end - start, scaled_words)
        dialogues.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Default,,0,0,0,,"
            f"{{\\fad(100,120)}}{karaoke}"
        )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {frame_width}
PlayResY: {frame_height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font_size},&H0000D7FF,&H00FFFFFF,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(dialogues) + "\n", encoding="utf-8")
    return segments, spoken_duration


def _media_duration(path: Path) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    try:
        return max(0.0, float(probe.stdout.strip()))
    except (TypeError, ValueError):
        return 0.0


def _uploaded_product_media_kind(path: Path) -> str:
    return "video" if path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} else "image"


def _uploaded_product_media_path(user_id: int, media_id: str) -> Optional[Path]:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", media_id or "")
    if not safe_id:
        return None
    candidates = sorted(_PRODUCT_MEDIA_UPLOAD_DIR.glob(f"{user_id}_{safe_id}.*"))
    if not candidates:
        return None
    root = _PRODUCT_MEDIA_UPLOAD_DIR.resolve()
    path = candidates[0].resolve()
    try:
        inside_root = path.is_relative_to(root)
    except AttributeError:
        inside_root = root in path.parents
    if not path.is_file() or not inside_root:
        return None
    return path


def _validated_product_media_paths(user_id: int, media_ids: List[str]) -> List[str]:
    paths: List[str] = []
    for media_id in media_ids[:12]:
        path = _uploaded_product_media_path(user_id, media_id)
        if path and str(path) not in paths:
            paths.append(str(path))
    return paths


def _scene_prefers_product_media(scene: str) -> bool:
    """Use real product media only for ad beats where the product must be seen."""
    folded = _ascii_fold(scene)
    product_markers = (
        "hero product media", "product reveal", "demo proof", "result shot",
        "detail shot", "final frame", "cta shot", "san pham that", "quay thao tac dung that",
        "can canh san pham", "bao bi", "kich thuoc", "phu kien", "chat lieu",
    )
    context_markers = (
        "problem shot", "model problem scene", "model use scene", "model lifestyle shot",
        "objection shot", "lifestyle shot", "nguoi thuoc nhom",
        "gap dung van de", "diem han che", "boi canh that",
    )
    if any(marker in folded for marker in context_markers):
        return False
    return any(marker in folded for marker in product_markers)


def _scene_requires_model_context(scene: str) -> bool:
    folded = _ascii_fold(scene)
    return any(
        marker in folded
        for marker in ("model problem scene", "model use scene", "model lifestyle shot")
    )


def _product_video_media_for_model_scene(product_media_paths: List[Path], scene_index: int) -> Optional[Path]:
    video_paths = [path for path in product_media_paths if _uploaded_product_media_kind(path) == "video"]
    if not video_paths:
        return None
    return video_paths[scene_index % len(video_paths)]


def _local_ai_image_model_id() -> str:
    return _env_first("IMAGE_AI_MODEL", "CPU_IMAGE_MODEL") or "stabilityai/sd-turbo"


def _local_ai_model_is_low_quality_for_people() -> bool:
    return "sd-turbo" in _local_ai_image_model_id().lower()


def _product_media_animation_for_scene(scene: str, index: int) -> str:
    folded = _ascii_fold(scene)
    if "product reveal" in folded or "detail shot" in folded or "can canh" in folded:
        return "zoomin"
    if "result shot" in folded or "before" in folded or "after" in folded:
        return "slideleft"
    if "cta shot" in folded or "final frame" in folded:
        return "zoomout"
    return ("zoomin", "slideleft", "zoomout", "slideright")[index % 4]


def _synthesize_creator_cues(
    scenes: List[str], output_dir: Path, language: str, voice: Optional[str], output_path: Path,
) -> tuple[List[float], List[List[int]]]:
    """Synthesize each displayed cue separately and concatenate the audio.

    Measuring every cue's real audio duration gives subtitle boundaries that
    follow the selected voice, including its pauses and speaking style.
    """
    cues = [chunk for scene in scenes for chunk in _balanced_caption_chunks(scene, max_chars=100, line_chars=52)]
    cue_dir = output_dir / "narration_cues"
    cue_dir.mkdir(parents=True, exist_ok=True)
    import edge_tts
    preset = voice or voice_for_language(language)
    preset_parts = preset.split("|")
    effective_voice = preset_parts[0]
    options = dict(part.split("=", 1) for part in preset_parts[1:] if "=" in part)
    paths: List[Path] = []
    durations: List[float] = []
    word_durations: List[List[int]] = []
    for index, cue in enumerate(cues):
        directed_cue = direct_voice_cue(cue, index, len(cues), language)
        cue_path = cue_dir / f"cue_{index:03d}.mp3"
        communicate = edge_tts.Communicate(
            directed_cue.text.replace("\n", " "), effective_voice,
            rate=directed_cue.rate or options.get("rate", "+0%"),
            pitch=directed_cue.pitch or options.get("pitch", "+0Hz"),
            boundary="WordBoundary",
        )
        measured_boundaries: List[tuple[int, int]] = []
        with cue_path.open("wb") as audio_file:
            for event in communicate.stream_sync():
                if event["type"] == "audio":
                    audio_file.write(event["data"])
                elif event["type"] == "WordBoundary":
                    measured_boundaries.append((event["offset"], event["duration"]))
        duration = _media_duration(cue_path)
        if duration <= 0:
            raise RuntimeError(f"KhÃ´ng Ä‘o Ä‘Æ°á»£c thá»i lÆ°á»£ng voice Ä‘oáº¡n {index + 1}")
        paths.append(cue_path)
        durations.append(duration)
        # Karaoke tags are sequential. Use the distance between Edge's real
        # word start offsets so natural inter-word pauses remain attached to
        # the word being spoken; the final word runs to the cue audio end.
        measured_words: List[int] = []
        for boundary_index, (offset, boundary_duration) in enumerate(measured_boundaries):
            if boundary_index + 1 < len(measured_boundaries):
                end_offset = measured_boundaries[boundary_index + 1][0]
            else:
                end_offset = max(offset + boundary_duration, round(duration * 10_000_000))
            measured_words.append(max(1, round((end_offset - offset) / 100_000)))
        word_durations.append(measured_words)
    concat_file = cue_dir / "concat.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)],
        capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "KhÃ´ng ghÃ©p Ä‘Æ°á»£c cÃ¡c Ä‘oáº¡n voice")[-4000:])
    return durations, word_durations


def _add_creator_voice_and_subtitles(
    video_path: Path, voice_path: Path, subtitles_path: Path,
    output_path: Path, duration: float,
) -> None:
    # On Windows, an absolute `D:/...` path inside a filtergraph is parsed as
    # `filename=D` followed by a new `:` option. Run FFmpeg in the subtitle
    # directory and pass only the generated relative filename instead. This
    # also avoids fragile cross-platform escaping of drive letters/spaces.
    subtitle_filter = f"subtitles=filename={subtitles_path.name}"
    # Never time-compress or stretch narration. The selected duration drives
    # script length; if real TTS happens to run longer, the visual timeline is
    # extended before this function is called. A shorter voice gets silence at
    # the end rather than an unnaturally slowed reading.
    audio_filters = ["apad"]
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(voice_path),
        "-vf", subtitle_filter, "-filter:a", ",".join(audio_filters),
        "-map", "0:v:0", "-map", "1:a:0", "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", WEB_RENDER_PRESET, "-crf", str(WEB_RENDER_CRF),
        "-r", "30", "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(
        cmd, cwd=str(subtitles_path.parent), capture_output=True, text=True,
        check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg voice/subtitle render failed")[-4000:])


def _run_creator_job(job_id: str, body: CreatorJobBody) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Äang dá»±ng video tá»« Ã½ tÆ°á»Ÿng...")
        output_dir = _OUTPUT_BASE_DIR / "web_jobs" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = _split_creator_script(body.topic, body.script, body.target_language)
        narration_scenes = _split_creator_script(
            body.topic,
            body.narration_script or _creator_narration_text_from_topic(
                body.topic, body.target_language, body.duration_seconds,
            ),
            body.target_language,
        )
        selected_duration = max(10, min(1200, int(body.duration_seconds or 30)))

        # Measure natural speech before rendering visuals. This makes timing a
        # real content constraint rather than changing speaking rate at the
        # final FFmpeg step.
        store.update_job(job_id, progress_note="Äang táº¡o giá»ng Ä‘á»c tá»± nhiÃªn Ä‘á»ƒ Ä‘o timeline...")
        voice_path = output_dir / "narration.mp3"
        cue_durations, cue_word_durations = _synthesize_creator_cues(
            narration_scenes, output_dir, body.target_language, body.tts_voice, voice_path,
        )
        if not voice_path.exists() or voice_path.stat().st_size < 1024:
            raise RuntimeError("TTS khÃ´ng táº¡o Ä‘Æ°á»£c file giá»ng Ä‘á»c há»£p lá»‡")
        voice_duration = _media_duration(voice_path)
        if voice_duration <= 0:
            raise RuntimeError("KhÃ´ng Ä‘o Ä‘Æ°á»£c thá»i lÆ°á»£ng giá»ng Ä‘á»c")
        final_duration = max(float(selected_duration), voice_duration)
        scene_duration = max(2.5, final_duration / len(scenes))
        logger.info(
            "Creator natural timing: selected=%.2fs voice=%.2fs final=%.2fs scenes=%s scene=%.2fs",
            selected_duration, voice_duration, final_duration, len(scenes), scene_duration,
        )
        if body.aspect_ratio == "16:9":
            width, height = 1920, 1080
        else:
            width, height = 1080, 1920

        translated_stock_context = _translate_stock_texts_to_english([
            body.topic, *scenes, *narration_scenes,
        ])
        translated_topic = translated_stock_context[0]
        translated_scenes = translated_stock_context[1:1 + len(scenes)]
        translated_narration = translated_stock_context[1 + len(scenes):]
        clip_paths: List[Path] = []
        video_fallback_count = 0
        image_provider = body.image_provider.lower()
        product_media_paths = [
            Path(path) for path in (body.product_media_paths or [])
            if Path(path).exists() and Path(path).is_file()
        ]
        video_backend = _creator_video_backend() if image_provider == "ai_video" else None
        ai_scene_prompts: List[str] = []
        story_seed: Optional[int] = None
        if image_provider in ("ai", "cpu_ai", "ai_video"):
            store.update_job(job_id, progress_note="AI Ä‘ang láº­p storyboard vÃ  máº¡ch hÃ¬nh áº£nh...")
            story_bible, ai_scene_prompts = _creator_image_story_prompts(
                translated_topic, translated_scenes, translated_narration,
            )
            story_seed = int.from_bytes(story_bible.encode("utf-8")[:8].ljust(8, b"0"), "little") % (2**31)
            logger.info("Creator AI story bible: %s", story_bible)
        ai_keyframes: List[Path] = []
        if image_provider == "ai_video":
            # Generate all coherent keyframes in one pass, then release SD so
            # LTX has as much VRAM as possible while animating the scenes.
            for idx, prompt in enumerate(ai_scene_prompts):
                store.update_job(
                    job_id,
                    progress_note=f"AI Ä‘ang táº¡o storyboard {idx + 1}/{len(ai_scene_prompts)}...",
                )
                ai_keyframes.append(_generate_ai_image(
                    prompt, output_dir / f"keyframe_{idx:02d}.jpg",
                    body.aspect_ratio, story_seed=story_seed, for_video=True,
                ))
            _release_image_ai_pipeline()
        for idx, scene in enumerate(scenes):
            clip_path = output_dir / f"scene_{idx:02d}.mp4"
            # Visual and narration lists may have different lengths. Map
            # them proportionally so each shot follows what is being spoken
            # around the same point in the video.
            narration_idx = min(
                len(translated_narration) - 1,
                int(idx * len(translated_narration) / max(1, len(scenes))),
            )
            queries = _creator_stock_queries(
                translated_topic,
                translated_scenes[idx],
                translated_narration[narration_idx],
            )
            logger.info("Creator scene %s/%s stock queries: %s", idx + 1, len(scenes), queries)
            media = None
            model_product_video = (
                _product_video_media_for_model_scene(product_media_paths, idx)
                if _scene_requires_model_context(scene) else None
            )
            if model_product_video:
                _render_product_ad_clip(
                    model_product_video, "video", clip_path, width, height,
                    scene_duration, scene, idx,
                )
                media = {
                    "type": "video", "url": str(model_product_video),
                    "provider": "Uploaded product demo video",
                }
                store.update_job(
                    job_id,
                    progress_note=f"Da dung video demo that cho canh nguoi mau {idx + 1}/{len(scenes)}...",
                )
            elif product_media_paths and _scene_prefers_product_media(scene):
                source_path = product_media_paths[idx % len(product_media_paths)]
                media_type = _uploaded_product_media_kind(source_path)
                _render_product_ad_clip(
                    source_path, media_type, clip_path, width, height,
                    scene_duration, scene, idx,
                )
                media = {
                    "type": media_type, "url": str(source_path),
                    "provider": "Product media",
                }
                store.update_job(
                    job_id,
                    progress_note=f"Da dung canh {idx + 1}/{len(scenes)} tu product media...",
                )
            elif image_provider in ("ai", "cpu_ai"):
                try:
                    if _scene_requires_model_context(scene) and _local_ai_model_is_low_quality_for_people() and not _remote_creator_image_available():
                        raise RuntimeError(
                            "Local AI dang dung sd-turbo, khong du chat luong de tao canh nguoi mau cam/dung san pham that. "
                            "Hay upload video demo that cua san pham, chon Stock voi Pexels/Pixabay, hoac cau hinh HUGGINGFACE_API_KEY / DALL-E."
                        )
                    store.update_job(
                        job_id,
                        progress_note=f"AI Ä‘ang sinh khung hÃ¬nh {idx + 1}/{len(scenes)}...",
                    )
                    # Priority: Hugging Face (free) > DALL-E (paid) > Local SD-Turbo (free)
                    hf_api_key = _env_first("HUGGINGFACE_API_KEY")
                    use_dalle = _env_first("USE_DALLE_IMAGES", "false").lower() == "true"
                    
                    if hf_api_key:
                        # Use Hugging Face Inference API (free)
                        try:
                            ai_path = _generate_huggingface_image(
                                ai_scene_prompts[idx], output_dir / f"ai_{idx:02d}.jpg",
                                body.aspect_ratio,
                            )
                        except Exception as hf_exc:
                            logger.warning(f"Hugging Face failed, falling back to local SD-Turbo: {hf_exc}")
                            local_ok, local_reason = _local_creator_image_runtime_available()
                            if not local_ok:
                                raise RuntimeError(
                                    f"Hugging Face lỗi và local AI image chưa dùng được ({local_reason})"
                                ) from hf_exc
                            ai_path = _generate_ai_image(
                                ai_scene_prompts[idx], output_dir / f"ai_{idx:02d}.jpg",
                                body.aspect_ratio, story_seed=story_seed,
                            )
                    elif use_dalle and OPENAI_AVAILABLE:
                        # Use DALL-E (paid, better quality)
                        try:
                            ai_path = _generate_dalle_image(
                                ai_scene_prompts[idx], output_dir / f"ai_{idx:02d}.jpg",
                                body.aspect_ratio,
                            )
                        except Exception as dalle_exc:
                            logger.warning(f"DALL-E failed, falling back to local SD-Turbo: {dalle_exc}")
                            local_ok, local_reason = _local_creator_image_runtime_available()
                            if not local_ok:
                                raise RuntimeError(
                                    f"DALL-E lỗi và local AI image chưa dùng được ({local_reason})"
                                ) from dalle_exc
                            ai_path = _generate_ai_image(
                                ai_scene_prompts[idx], output_dir / f"ai_{idx:02d}.jpg",
                                body.aspect_ratio, story_seed=story_seed,
                            )
                    else:
                        # Use local SD-Turbo (free, lower quality)
                        if _scene_requires_model_context(scene) and _local_ai_model_is_low_quality_for_people():
                            raise RuntimeError(
                                "Local sd-turbo bi chan cho canh nguoi mau/product-in-hand vi thuong tao tay/mat/san pham sai. "
                                "Dung video demo upload, Stock, HuggingFace, hoac DALL-E cho canh nay."
                            )
                        ai_path = _generate_ai_image(
                            ai_scene_prompts[idx], output_dir / f"ai_{idx:02d}.jpg",
                            body.aspect_ratio, story_seed=story_seed,
                        )
                    _render_stock_clip(
                        ai_path, "image", clip_path, width, height,
                        scene_duration, body.transition,
                    )
                    media = {"type": "image", "url": str(ai_path), "provider": f"AI sinh áº£nh ({_IMAGE_AI_DEVICE})"}
                except Exception as exc:
                    logger.exception("AI image generation failed for scene=%s", scene)
                    raise RuntimeError(f"AI khÃ´ng sinh Ä‘Æ°á»£c khung hÃ¬nh {idx + 1}: {exc}") from exc
            elif image_provider == "ai_video":
                try:
                    store.update_job(
                        job_id,
                        progress_note=(
                            f"GPU Ä‘ang sinh video cáº£nh {idx + 1}/{len(scenes)} "
                            f"báº±ng {str(video_backend).upper()}..."
                        ),
                    )
                    raw_video_path = output_dir / f"ai_video_{idx:02d}.mp4"
                    if video_backend == "svd":
                        raw_video = _generate_svd_video_isolated(
                            ai_keyframes[idx], raw_video_path,
                            body.aspect_ratio, scene_duration, (story_seed or 0) + idx,
                        )
                    else:
                        raw_video = _generate_ai_video(
                            ai_keyframes[idx], ai_scene_prompts[idx], raw_video_path,
                            body.aspect_ratio, scene_duration, (story_seed or 0) + idx,
                        )
                    _render_stock_clip(
                        raw_video, "video", clip_path, width, height,
                        scene_duration, body.transition,
                    )
                    media = {
                        "type": "video", "url": str(raw_video),
                        "provider": f"AI Sinh Video ({str(video_backend).upper()})",
                    }
                except Exception as exc:
                    # Low-memory video inference is experimental. Preserve a
                    # complete usable job when one scene fails or its isolated
                    # worker crashes by animating the already-generated frame.
                    logger.exception(
                        "AI video generation failed for scene=%s; using safe keyframe fallback", scene,
                    )
                    video_fallback_count += 1
                    store.update_job(
                        job_id,
                        progress_note=f"SVD lá»—i á»Ÿ cáº£nh {idx + 1}; Ä‘ang dÃ¹ng chuyá»ƒn Ä‘á»™ng áº£nh an toÃ n...",
                    )
                    _render_stock_clip(
                        ai_keyframes[idx], "image", clip_path, width, height,
                        scene_duration, body.transition if body.transition != "none" else "zoomin",
                    )
                    media = {
                        "type": "image", "url": str(ai_keyframes[idx]),
                        "provider": "AI sinh áº£nh (fallback tá»« SVD)",
                    }
            else:
                for query in queries:
                    media = _search_stock_media(query, body.aspect_ratio)
                    if media:
                        break
            if media and not media["provider"].startswith(("AI sinh áº£nh", "AI Sinh Video", "Product media")):
                try:
                    source_path = _download_stock_media(media, output_dir / f"stock_{idx:02d}")
                    _render_stock_clip(
                        source_path, media["type"], clip_path, width, height,
                        scene_duration, body.transition,
                    )
                    store.update_job(
                        job_id,
                        progress_note=f"ÄÃ£ láº¥y cáº£nh {idx + 1}/{len(scenes)} tá»« {media['provider']}...",
                    )
                except Exception:
                    logger.exception("Stock media render failed for scene=%s", scene)
                    media = None

            if not media and _scene_requires_model_context(scene):
                raise RuntimeError(
                    "Cảnh người mẫu cần Stock footage hoặc AI Image thật. Hiện không tạo được cảnh người mẫu, "
                    "nên hệ thống đã dừng thay vì fallback thành ảnh sản phẩm chạy qua lại. "
                    "Hãy cấu hình PEXELS_API_KEY/PIXABAY_API_KEY, hoặc bật DALL-E/HuggingFace image provider."
                )

            if not media and product_media_paths:
                source_path = product_media_paths[idx % len(product_media_paths)]
                media_type = _uploaded_product_media_kind(source_path)
                _render_product_ad_clip(
                    source_path, media_type, clip_path, width, height,
                    scene_duration, scene, idx,
                )
                media = {
                    "type": media_type, "url": str(source_path),
                    "provider": "Product media fallback",
                }
                store.update_job(
                    job_id,
                    progress_note=f"Không có stock phù hợp; dùng product media fallback cho cảnh {idx + 1}/{len(scenes)}...",
                )

            if not media:
                raise RuntimeError(
                    "KhÃ´ng láº¥y Ä‘Æ°á»£c áº£nh/video stock. HÃ£y kiá»ƒm tra PEXELS_API_KEY trong .env "
                    "vÃ  khá»Ÿi Ä‘á»™ng láº¡i web; há»‡ thá»‘ng khÃ´ng táº¡o video ná»n chá»¯ thay tháº¿ ná»¯a."
                )
            clip_paths.append(clip_path)
            store.update_job(job_id, progress_note=f"ÄÃ£ dá»±ng {idx + 1}/{len(scenes)} cáº£nh...")

        visual_path = output_dir / "visual_timeline.mp4"
        store.update_job(job_id, progress_note="Äang ghÃ©p vÃ  táº¡o hiá»‡u á»©ng chuyá»ƒn cáº£nh...")
        # Entrance/exit animation is already rendered into every scene.
        # A timestamp-safe concat avoids FFmpeg 7's broken multi-xfade path.
        _render_creator_timeline(
            clip_paths, visual_path, scene_duration, final_duration, "none",
        )

        # Subtitle timing follows the untouched natural TTS exactly.
        spoken_duration = voice_duration
        subtitles_path = output_dir / "subtitles.ass"
        segments, _ = _write_creator_subtitles(
            narration_scenes, subtitles_path, spoken_duration, cue_durations, width, height,
            cue_word_durations,
        )
        store.set_job_segments(job_id, segments)

        store.update_job(job_id, progress_note="Äang ghÃ©p voice vÃ  Ä‘Ã³ng subtitle...")
        output_path = output_dir / "output_generated.mp4"
        _add_creator_voice_and_subtitles(
            visual_path, voice_path, subtitles_path, output_path, final_duration,
        )
        store.update_job(
            job_id, status="done",
            progress_note=(
                f"HoÃ n táº¥t Â· {video_fallback_count} cáº£nh dÃ¹ng chuyá»ƒn Ä‘á»™ng áº£nh fallback"
                if video_fallback_count else "HoÃ n táº¥t"
            ),
            final_video_path=str(output_path), title=body.topic.strip()[:80],
        )
    except Exception as exc:
        logger.exception("Creator job %s failed", job_id)
        store.update_job(job_id, status="error", error=f"{exc}\n{traceback.format_exc()[-1500:]}")
        _refund_job_credits(job)
    finally:
        _running_tasks.pop(job_id, None)


@app.post("/api/jobs")
async def create_job(body: NewJobBody, user_id: int = Depends(get_current_user_id)):
    if not body.url.strip():
        raise HTTPException(400, "Thiáº¿u Ä‘Æ°á»ng link video")

    user = store.get_user_by_id(user_id)
    if JOB_COST_CREDITS > 0 and user["credits"] < JOB_COST_CREDITS:
        raise HTTPException(
            402,
            f"KhÃ´ng Ä‘á»§ credit (cÃ²n {user['credits']}, cáº§n {JOB_COST_CREDITS}). "
            "LiÃªn há»‡ admin Ä‘á»ƒ Ä‘Æ°á»£c cáº¥p thÃªm.",
        )

    logo_path = None
    if body.logo_path:
        # body.logo_path is actually the opaque id POST /api/upload-logo
        # returned â€” resolve it back to a real file path scoped to this
        # user, so one user can't reference another's uploaded logo file
        # just by guessing/reusing an id.
        candidate = _LOGO_UPLOAD_DIR / f"{user_id}_{body.logo_path}"
        matches = list(_LOGO_UPLOAD_DIR.glob(f"{user_id}_{body.logo_path}.*"))
        if matches:
            logo_path = str(matches[0])
        elif candidate.exists():
            logo_path = str(candidate)

    job = store.create_job(
        user_id, body.url.strip(), body.target_language,
        source_language=body.source_language or "auto",
        logo_path=logo_path,
        logo_corner=body.logo_corner or "bottom_right",
        logo_size_px=body.logo_size_px or 120,
        tts_voice=body.tts_voice or None,
        review_mode=body.review_before_render,
        animated_subtitle_config=body.animated_subtitle_config,
        video_template_config=body.video_template_config,
    )
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    task = asyncio.create_task(_run_job(job.id))
    _running_tasks[job.id] = task
    return job.to_dict()


@app.post("/api/creator/jobs")
async def create_creator_job(body: CreatorJobBody, user_id: int = Depends(get_current_user_id)):
    if not body.topic.strip():
        raise HTTPException(400, "Thiáº¿u chá»§ Ä‘á» video")
    if body.aspect_ratio not in ("9:16", "16:9"):
        raise HTTPException(400, "Tá»· lá»‡ khung hÃ¬nh khÃ´ng há»£p lá»‡")
    if body.transition not in CREATOR_TRANSITIONS:
        raise HTTPException(400, "Hiá»‡u á»©ng chuyá»ƒn cáº£nh khÃ´ng há»£p lá»‡")
    if not 10 <= int(body.duration_seconds or 0) <= 1200:
        raise HTTPException(400, "Thá»i lÆ°á»£ng video pháº£i tá»« 10 giÃ¢y Ä‘áº¿n 20 phÃºt")
    if body.image_provider not in ("stock", "ai", "cpu_ai", "ai_video"):
        raise HTTPException(400, "Nguá»“n hÃ¬nh áº£nh khÃ´ng há»£p lá»‡")
    product_media_paths = _validated_product_media_paths(user_id, body.product_media_paths or [])
    body.product_media_paths = product_media_paths
    if body.image_provider in ("ai", "cpu_ai", "ai_video"):
        _validate_creator_ai_runtime(body.image_provider)
    if body.image_provider == "stock" and not product_media_paths and not _env_first("PEXELS_API_KEY", "PEXELS_KEY", "PIXABAY_API_KEY", "PIXABAY_KEY"):
        raise HTTPException(
            503,
            "ChÆ°a cáº¥u hÃ¬nh kho áº£nh. ThÃªm PEXELS_API_KEY vÃ o file .env rá»“i khá»Ÿi Ä‘á»™ng láº¡i web.",
        )
    user = store.get_user_by_id(user_id)
    if JOB_COST_CREDITS > 0 and user["credits"] < JOB_COST_CREDITS:
        raise HTTPException(402, f"KhÃ´ng Ä‘á»§ credit (cÃ²n {user['credits']}, cáº§n {JOB_COST_CREDITS})")

    job = store.create_job(
        user_id,
        f"creator:{body.topic.strip()}",
        body.target_language,
        source_language=f"creator:{'ai' if body.image_provider == 'cpu_ai' else body.image_provider}",
    )
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    task = asyncio.create_task(asyncio.to_thread(_run_creator_job, job.id, body))
    _running_tasks[job.id] = task
    return job.to_dict()


@app.get("/api/creator/capabilities")
def creator_capabilities(user_id: int = Depends(get_current_user_id)):
    """Report machine-specific creator options so unsafe choices can be disabled."""
    result = {
        "ai_image": False, "ai_video": False,
        "gpu_name": None, "vram_gb": 0.0, "ai_video_reason": None,
        "ai_video_backend": None,
    }
    try:
        _validate_creator_ai_runtime("cpu_ai")
        result["ai_image"] = True
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_properties(0)
            result["gpu_name"] = gpu.name
            result["vram_gb"] = round(gpu.total_memory / (1024 ** 3), 1)
        try:
            _validate_creator_ai_runtime("ai_video")
            result["ai_video"] = True
            result["ai_video_backend"] = _creator_video_backend().upper()
        except HTTPException as exc:
            result["ai_video_reason"] = str(exc.detail)
    except HTTPException as exc:
        result["ai_video_reason"] = str(exc.detail)
    return result


@app.post("/api/creator/suggestions")
def creator_suggestions(body: CreatorSuggestionBody, user_id: int = Depends(get_current_user_id)):
    if not body.topic.strip():
        raise HTTPException(400, "Thiáº¿u chá»§ Ä‘á» video")
    if not 10 <= int(body.duration_seconds or 0) <= 1200:
        raise HTTPException(400, "Thá»i lÆ°á»£ng ná»™i dung pháº£i tá»« 10 giÃ¢y Ä‘áº¿n 20 phÃºt")
    topic = body.topic.strip()
    language = body.target_language or "vi"
    
    # Use provider from request body, fallback to env var, then auto
    provider = (body.provider or _env_first("CREATOR_AI_PROVIDER") or "auto").lower()
    
    providers = []
    if provider in ("auto", "ollama"):
        providers.append(("Ollama", _ollama_creator_suggestions))
    if provider in ("auto", "openrouter") and _env_first("OPENROUTER_API_KEY"):
        providers.append(("OpenRouter", _openrouter_creator_suggestions))
    if provider in ("auto", "gemini") and _env_first("GEMINI_API_KEY", "GOOGLE_AI_API_KEY"):
        providers.append(("Gemini", _creator_ai_suggestions))
    if provider in ("auto", "openai") and _env_first("OPENAI_API_KEY"):
        providers.append(("OpenAI", _openai_creator_suggestions))
    if providers:
        cache_key = (
            provider, topic.lower(), language, body.aspect_ratio,
            int(body.duration_seconds or 30), body.transition,
            str(body.advanced_options),
        )
        with _CREATOR_AI_LOCK:
            # Evict old entries before checking cache
            _evict_old_cache_entries()
            
            cached = _CREATOR_AI_CACHE.get(cache_key)
            if cached and time.time() - cached[0] < _CREATOR_AI_CACHE_TTL_SECONDS:
                result = dict(cached[1])
                result["cached"] = True
                return result
            errors = []
            for provider_name, generate in providers:
                try:
                    result = generate(body)
                    result = _enforce_creator_entity_consistency(result, topic, language)
                    result = _postprocess_creator_suggestion_quality(result, topic, language, body.duration_seconds)
                    result = _enforce_creator_entity_consistency(result, topic, language)
                    result = _validate_creator_suggestion_timing(result, body.duration_seconds)
                    result = _annotate_creator_suggestion_timing(result, body.duration_seconds)
                    _CREATOR_AI_CACHE[cache_key] = (time.time(), dict(result))
                    return result
                except Exception as exc:
                    logger.warning("%s creator suggestions failed: %s", provider_name, exc)
                    errors.append(f"{provider_name}: {exc}")
                    if provider != "auto":
                        break
        # Suggestions are editing aids, so an unavailable optional AI service
        # must not make all three Generate buttons unusable.
        result = {
            "keywords": _creator_seo_keywords_from_topic(topic, language, body.duration_seconds),
            "visual_brief": _creator_script_text_from_topic(topic, language, body.duration_seconds),
            "script": _creator_script_text_from_topic(topic, language, body.duration_seconds),
            "narration_script": _creator_narration_text_from_topic(topic, language, body.duration_seconds),
            "generator": "template", "model": None,
            "warning": "AI táº¡m thá»i khÃ´ng pháº£n há»“i; Ä‘ang dÃ¹ng ná»™i dung local. " + " | ".join(errors),
        }
        result = _postprocess_creator_suggestion_quality(result, topic, language, body.duration_seconds)
        result = _enforce_creator_entity_consistency(result, topic, language)
        return _annotate_creator_suggestion_timing(result, body.duration_seconds)
    result = {
        "keywords": _creator_seo_keywords_from_topic(topic, language, body.duration_seconds),
        "visual_brief": _creator_script_text_from_topic(topic, language, body.duration_seconds),
        "script": _creator_script_text_from_topic(topic, language, body.duration_seconds),
        "narration_script": _creator_narration_text_from_topic(topic, language, body.duration_seconds),
        "generator": "template", "model": None,
        "warning": "AI chÆ°a Ä‘Æ°á»£c cáº¥u hÃ¬nh hoáº·c táº¡m thá»i khÃ´ng pháº£n há»“i; Ä‘ang dÃ¹ng ná»™i dung máº«u local.",
    }
    result = _postprocess_creator_suggestion_quality(result, topic, language, body.duration_seconds)
    result = _enforce_creator_entity_consistency(result, topic, language)
    return _annotate_creator_suggestion_timing(result, body.duration_seconds)


_AFFILIATE_RISK_PATTERNS = [
    (re.compile(r"\b(100%|chac chan|bao dam|cam ket|than toc|ngay lap tuc)\b", re.I), "Claim sounds absolute; soften it and tie it to real usage."),
    (re.compile(r"\b(tri benh|chua benh|het benh|giam can|moc toc|trang da|het mun)\b", re.I), "Health/beauty result claim needs evidence and careful wording."),
    (re.compile(r"\b(tot nhat|so 1|re nhat|duy nhat)\b", re.I), "Superlative claim needs proof or comparison context."),
    (re.compile(r"\b(fake|nhai|replica|hang gia)\b", re.I), "Avoid promoting counterfeit or misleading product positioning."),
]


def _split_user_lines(value: str) -> List[str]:
    parts = re.split(r"[\n,;]+", value or "")
    return [part.strip(" -\t") for part in parts if part.strip(" -\t")]


def _affiliate_fragment(value: str) -> str:
    text = " ".join(str(value or "").strip().rstrip(".").split())
    if not text:
        return text
    return text[:1].lower() + text[1:]


def _affiliate_expressive_line(text: str) -> str:
    text = " ".join(str(text or "").split())
    text = re.sub(r"^nhÆ°ng\b", "NhÆ°ng,", text, flags=re.I)
    text = re.sub(r"\bnáº¿u báº¡n\b", "Náº¿u báº¡n", text, flags=re.I)
    return text


def _affiliate_product_voice_name(product: str) -> str:
    """Use a short spoken product name; keep the full name for title/caption."""
    text = " ".join(str(product or "").split())
    text = re.sub(r"^(?:chai|lá»|lo|há»™p|hop|tÃºi|tui|gÃ³i|goi|bá»™|bo)\s+", "", text, flags=re.I)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|gram|chai|tÃºi|goi|gÃ³i)\b", "", text, flags=re.I)
    text = re.split(
        r"\b(?:giá»¯|giu|lÆ°u|luu|thÆ¡m|thom|trÃªn|tren|cho|dÃ nh|danh|phÃ¹ há»£p|phu hop|hÆ°Æ¡ng thÆ¡m|huong thom)\b",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    words = [word.strip(" -,.") for word in text.split() if word.strip(" -,.")]
    if len(words) > 8:
        words = words[:8]
    short = " ".join(words).strip(" -,.")
    return short or product.strip()


def _infer_affiliate_points(body: AffiliateReviewBody, language: str) -> tuple[List[str], List[str], List[str]]:
    product = body.product_name.strip()
    experience = body.real_experience.strip()
    audience = body.audience.strip()
    claims = _split_user_lines(body.product_claims)
    pros = _split_user_lines(body.pros)
    cons = _split_user_lines(body.cons)

    if language == "vi":
        inferred_claims = [
            f"{product} có thể giải quyết một nhu cầu khá cụ thể trong sinh hoạt hằng ngày",
            "Nên đối chiếu mô tả của shop với trải nghiệm thực tế trước khi mua",
        ]
        inferred_pros = [
            "Sản phẩm cho cảm giác hữu ích trong những tình huống nhỏ, cần xử lý nhanh",
            f"Hợp hơn với {audience}" if audience else "Hợp hơn với người có nhu cầu rõ ràng và ngân sách phù hợp",
        ]
        inferred_cons = [
            "Kết quả thực tế vẫn phụ thuộc vào cách dùng và kỳ vọng của mỗi người",
            "Nên kiểm tra giá, bảo hành, đánh giá mới nhất và điều kiện đổi trả trước khi mua",
        ]
    else:
        inferred_claims = [
            f"{product} may solve a specific everyday use case",
            "Compare the seller description with real usage before buying",
        ]
        inferred_pros = [
            f"Most useful real note: {experience}",
            f"Best fit for {audience}" if audience else "Best fit for buyers with a clear need and matching budget",
        ]
        inferred_cons = [
            "Results can vary by usage habits and expectations",
            "Check current price, warranty, recent reviews, and return terms before buying",
        ]
    return claims or inferred_claims, pros or inferred_pros, cons or inferred_cons


def _affiliate_compliance_warnings(body: AffiliateReviewBody) -> List[str]:
    haystack = " ".join([
        body.product_name, body.product_claims, body.pros, body.cons,
        body.audience, body.real_experience,
    ])
    normalized = unicodedata.normalize("NFKD", haystack.lower().replace("Ä‘", "d"))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    warnings: List[str] = []
    for pattern, message in _AFFILIATE_RISK_PATTERNS:
        if pattern.search(normalized) and message not in warnings:
            warnings.append(message)
    if len(body.real_experience.strip().split()) < 8:
        warnings.append("Real experience is too thin; add what you personally tested, noticed, or measured.")
    return warnings


def _affiliate_seo_tags(product: str, platform: str, language: str) -> List[str]:
    compact_product = re.sub(r"[^0-9A-Za-z]+", "", unicodedata.normalize("NFKD", product).encode("ascii", "ignore").decode("ascii")).lower()[:30]
    platform_tag = re.sub(r"[^0-9A-Za-z]+", "", platform or "").lower()[:24]
    if language == "vi":
        base = [
            "reviewthat", "codangmuakhong", "truockhimuahang", "kinhnghiemmuasam",
            "muasamthongminh", "tiktokshop", "affiliate",
        ]
    else:
        base = [
            "honestreview", "worthbuying", "beforeyoubuy", "shoppingtips",
            "productreview", "affiliate",
        ]
    return _dedupe_clean_terms([compact_product, platform_tag, *base], 12)


def _affiliate_creative_format(body: AffiliateReviewBody) -> str:
    value = (body.creative_format or "ugc_problem_solution").strip().lower()
    if value not in {"ugc_problem_solution", "demo_proof", "before_after"}:
        return "ugc_problem_solution"
    return value


def _affiliate_model_prompt(body: AffiliateReviewBody, language: str) -> str:
    prompt = " ".join(str(body.model_prompt or "").split())
    if prompt:
        return prompt[:220]
    audience = body.audience.strip()
    if language == "vi":
        if audience:
            return f"người mẫu thuộc nhóm {audience}, phong cách UGC tự nhiên, đang dùng sản phẩm trong bối cảnh đời thật"
        return "người mẫu UGC tự nhiên, ánh sáng rõ, biểu cảm tin cậy, đang dùng sản phẩm trong bối cảnh đời thật"
    if audience:
        return f"UGC model matching {audience}, natural real-life setting, using the product"
    return "natural UGC model, clear light, trustworthy expression, using the product in a real-life setting"


def _product_ad_timecodes(duration: int, scene_count: int) -> List[str]:
    duration = max(15, min(60, int(duration or 30)))
    step = duration / scene_count
    ranges = []
    for index in range(scene_count):
        start = round(index * step)
        end = round((index + 1) * step)
        if index == 0:
            start = 0
        if index == scene_count - 1:
            end = duration
        ranges.append(f"{start}-{end}s")
    return ranges


def _affiliate_product_ad_creative(body: AffiliateReviewBody) -> Dict[str, Any]:
    language = body.target_language or "vi"
    product = body.product_name.strip()
    spoken_product = _affiliate_product_voice_name(product)
    duration = max(15, min(60, int(body.duration_seconds or 30)))
    creative = _affiliate_creative_format(body)
    claims, pros, cons = _infer_affiliate_points(body, language)
    audience = body.audience.strip()
    experience = body.real_experience.strip()
    model_prompt = _affiliate_model_prompt(body, language)
    disclosure = (
        "Có gắn link sản phẩm trong phần mô tả."
        if language == "vi" else
        "Product link is included in the description."
    )
    if language != "vi":
        return _affiliate_product_ad_creative_en(
            body, product, spoken_product, duration, creative, claims, pros, cons, audience, experience, disclosure,
        )

    audience_text = audience or "người đang cân nhắc sản phẩm này"
    pro = _affiliate_fragment(pros[0])
    con = _affiliate_fragment(cons[0])
    claim = _affiliate_fragment(claims[0])
    if creative == "demo_proof":
        hook = f"Tôi test thật {spoken_product} trong 30 giây, đây là phần đáng xem nhất."
        angle = "demo thật"
        middle = [
            f"Đừng nhìn quảng cáo trước, hãy nhìn cách nó xử lý đúng nhu cầu của {audience_text}.",
            f"Trong lúc dùng thử, điểm tôi thấy rõ nhất là {pro}.",
            f"Tôi cũng không bỏ qua điểm cần cân nhắc: {con}.",
        ]
    elif creative == "before_after":
        hook = f"Trước và sau khi dùng {spoken_product}, khác biệt nằm ở chi tiết này."
        angle = "trước sau"
        middle = [
            f"Trước đó, vấn đề của {audience_text} thường là mất thời gian vì cách xử lý cũ.",
            f"Khi đưa sản phẩm vào tình huống thật, điểm nổi bật là {pro}.",
            f"Nhưng nếu bạn kỳ vọng sản phẩm hoàn hảo tuyệt đối, hãy nhớ {con}.",
        ]
    else:
        hook = f"Nếu bạn đang định mua {spoken_product}, xem đoạn test này trước đã."
        angle = "UGC review"
        middle = [
            f"Vấn đề không phải sản phẩm nghe hay thế nào, mà là nó có hợp với {audience_text} không.",
            f"Trải nghiệm thật của tôi: {_affiliate_expressive_line(experience)}.",
            f"Điểm đáng tiền nhất là {pro}.",
            f"Điểm cần tỉnh táo là {con}.",
        ]

    if duration <= 15:
        narration_lines = [
            hook,
            middle[0],
            f"Nếu bạn cần {claim}, đây là món đáng cân nhắc nhưng vẫn nên kiểm tra giá và đổi trả.",
        ]
        scene_count = 5
    elif duration >= 60:
        narration_lines = [
            hook,
            middle[0],
            middle[1],
            middle[2],
            "Tôi muốn người xem thấy thao tác thật, bề mặt thật, kích thước thật, không chỉ một góc quay đẹp.",
            f"Nếu bạn thuộc nhóm {audience_text}, sản phẩm này có lý do để xem tiếp.",
            "Còn nếu nhu cầu của bạn khác, hãy so sánh thêm vài lựa chọn trước khi chốt đơn.",
            "Kiểm tra giá hiện tại, bảo hành, đánh giá mới nhất và điều kiện đổi trả trước khi quyết định.",
        ]
        scene_count = 10
    else:
        narration_lines = [
            hook,
            *middle,
            f"Nếu bạn là {audience_text}, hãy xem giá hiện tại và điều kiện đổi trả trước khi quyết định.",
        ]
        scene_count = 7

    timecodes = _product_ad_timecodes(duration, scene_count)
    visual_lines = [
        f"{timecodes[0]} | HERO PRODUCT MEDIA: đặt {product} trên bàn sạch, ánh sáng rõ, khung dọc 9:16, subtitle hook xuất hiện từ voice.",
        f"{timecodes[1]} | MODEL PROBLEM SCENE: {model_prompt}; gặp đúng vấn đề trước khi dùng sản phẩm.",
        f"{timecodes[2]} | PRODUCT REVEAL: cận cảnh sản phẩm thật, bao bì, kích thước, phụ kiện, chất liệu, không dùng ảnh AI vẽ lại sản phẩm.",
        f"{timecodes[3]} | MODEL USE SCENE + DEMO PROOF: {model_prompt}; người mẫu dùng {product} trong bối cảnh thật, sản phẩm và kết quả cùng xuất hiện trong khung hình.",
        f"{timecodes[4]} | RESULT SHOT: so sánh trước/sau hoặc đặt kết quả cạnh cách làm cũ để người xem tự đánh giá.",
    ]
    if scene_count >= 7:
        visual_lines.extend([
            f"{timecodes[5]} | OBJECTION SHOT: quay điểm hạn chế hoặc điều cần kiểm tra: {cons[0]}.",
            f"{timecodes[6]} | CTA SHOT: trang sản phẩm, giá hiện tại, bảo hành/đổi trả và disclosure affiliate rõ ràng.",
        ])
    if scene_count >= 10:
        visual_lines.extend([
            f"{timecodes[7]} | MODEL LIFESTYLE SHOT: {model_prompt}; sản phẩm nằm trong bối cảnh thật, không tạo cảm giác stock chung chung.",
            f"{timecodes[8]} | DETAIL SHOT: macro vào chi tiết chứng minh claim: {claims[0]}.",
            f"{timecodes[9]} | FINAL FRAME: sản phẩm thật ở trung tâm, nền gọn, giữ khung ổn định cho CTA cuối.",
        ])

    title = f"{product}: {angle} có đáng mua không cho {audience_text}?"
    caption = (
        f"{angle.capitalize()} {product}: test thật, điểm nên mua, điểm cần cân nhắc và checklist trước khi chốt đơn. "
        f"{disclosure} #reviewthat #codangmuakhong #tiktokshop"
    )
    return {
        "generator": "product_ad_template",
        "model": None,
        "product_url": body.product_url,
        "title": title,
        "caption": caption,
        "hashtags": _affiliate_seo_tags(product, body.platform, language),
        "disclosure": disclosure,
        "narration_script": "\n".join(narration_lines),
        "broll_plan": "\n".join(visual_lines),
        "compliance_warnings": _affiliate_compliance_warnings(body),
        "duration_seconds": duration,
        "creative_format": creative,
        "model_prompt": model_prompt,
        "quality_notes": [
            "Built as a product ad, not a generic topic video.",
            "Uses hook -> problem -> product reveal -> proof/demo -> objection -> CTA.",
            "Product media should be uploaded; AI/stock should only fill context shots.",
        ],
    }


def _affiliate_product_ad_creative_en(
    body: AffiliateReviewBody,
    product: str,
    spoken_product: str,
    duration: int,
    creative: str,
    claims: List[str],
    pros: List[str],
    cons: List[str],
    audience: str,
    experience: str,
    disclosure: str,
) -> Dict[str, Any]:
    audience_text = audience or "people considering this product"
    hook = f"If you are thinking about buying {spoken_product}, watch this real test first."
    narration_lines = [
        hook,
        f"The question is not whether the ad sounds good, but whether it fits {audience_text}.",
        f"My real usage note: {experience}.",
        f"The strongest reason to consider it is {pros[0]}.",
        f"The part to check before buying is {cons[0]}.",
        f"Check current price, warranty, reviews, and return terms before deciding. {disclosure}",
    ]
    scene_count = 5 if duration <= 15 else 7 if duration < 60 else 10
    timecodes = _product_ad_timecodes(duration, scene_count)
    visual_lines = [
        f"{timecodes[0]} | HERO PRODUCT MEDIA: real {product} on a clean table, vertical frame, clear light.",
        f"{timecodes[1]} | PROBLEM SHOT: {audience_text} dealing with the exact problem before using it.",
        f"{timecodes[2]} | PRODUCT REVEAL: real packaging, size, material, accessories; do not redraw the product with AI.",
        f"{timecodes[3]} | DEMO PROOF: hands-on real use of {product}, product and result visible in one frame.",
        f"{timecodes[4]} | CTA SHOT: product page, current price area, return terms, and affiliate disclosure.",
    ]
    if scene_count >= 7:
        visual_lines.insert(4, f"{timecodes[4]} | RESULT SHOT: before-after comparison or side-by-side proof of {pros[0]}.")
        visual_lines.insert(5, f"{timecodes[5]} | OBJECTION SHOT: show the limitation or buying check: {cons[0]}.")
    return {
        "generator": "product_ad_template",
        "model": None,
        "product_url": body.product_url,
        "title": f"{product}: honest ad-style review for {audience_text}",
        "caption": f"Real product ad review for {product}: proof, limitation, and buying checklist. {disclosure}",
        "hashtags": _affiliate_seo_tags(product, body.platform, "en"),
        "disclosure": disclosure,
        "narration_script": "\n".join(narration_lines),
        "broll_plan": "\n".join(visual_lines),
        "compliance_warnings": _affiliate_compliance_warnings(body),
        "duration_seconds": duration,
        "creative_format": creative,
        "quality_notes": [
            "Built as a product ad, not a generic topic video.",
            "Uses hook -> problem -> product reveal -> proof/demo -> objection -> CTA.",
            "Upload real product media; AI/stock should only fill context shots.",
        ],
    }


def _affiliate_strengthen_review_output(body: AffiliateReviewBody, result: Dict[str, Any]) -> Dict[str, Any]:
    language = body.target_language or "vi"
    product = body.product_name.strip()
    spoken_product = _affiliate_product_voice_name(product)
    audience = body.audience.strip()
    audience_tail = audience if audience else ("nguoi dang can nhac mua san pham nay" if language == "vi" else "people considering this product")
    upgraded = dict(result)
    narration_lines = [line.strip() for line in str(upgraded.get("narration_script") or "").splitlines() if line.strip()]
    if language == "vi":
        hook = f"Äá»«ng mua vá»™i {spoken_product} náº¿u báº¡n chÆ°a tháº¥y pháº§n test tháº­t nÃ y."
        upgraded["title"] = f"{product} co dang mua khong? Review that cho {audience_tail}"
        upgraded["caption"] = (
            f"Review that {product}: ai nen mua, ai nen can nhac, va diem can check truoc khi chot don. "
            f"{upgraded.get('disclosure', '')} #reviewthat #codangmuakhong #affiliate"
        ).strip()
        proof_lines = [
            f"Hook 0-3s: cam {product} tren tay, zoom vao chi tiet dang nghi ngo nhat.",
            f"Proof shot: quay thao tac dung that cua {product} trong boi canh cua {audience_tail}.",
            "Close-up: bao bi, chat lieu, kich thuoc, phu kien va chi tiet de nguoi xem tu danh gia.",
            "Before-after or comparison: dat san pham canh cach lam cu hoac vat doi chieu de thay khac biet.",
            "Risk shot: quay diem han che/diem can kiem tra, khong chi quay phan dep.",
            "CTA shot: man hinh gia, chinh sach doi tra, disclosure affiliate va loi nhac tu kiem tra lai.",
        ]
    else:
        hook = f"Do not buy {spoken_product} until you see this real test."
        upgraded["title"] = f"{product} worth buying? Honest review for {audience_tail}"
        upgraded["caption"] = (
            f"Honest {product} review: who should buy, who should skip, and what to check first. "
            f"{upgraded.get('disclosure', '')} #honestreview #worthbuying #affiliate"
        ).strip()
        proof_lines = [
            f"Hook 0-3s: hold {product} and zoom into the most questionable detail.",
            f"Proof shot: show a real hands-on use case for {audience_tail}.",
            "Close-up: packaging, material, size, accessories, and details viewers can judge.",
            "Before-after or comparison shot against the old method or a similar item.",
            "Risk shot: show the limitation or check point, not only the flattering angle.",
            "CTA shot: current price area, return policy, affiliate disclosure, and reminder to verify.",
        ]
    if not narration_lines or _is_weak_creator_hook(narration_lines[0], language):
        narration_lines = [hook, *narration_lines[1:]]
    else:
        narration_lines[0] = hook
    upgraded["narration_script"] = "\n".join(narration_lines)
    original_broll = [line.strip() for line in str(upgraded.get("broll_plan") or "").splitlines() if line.strip()]
    upgraded["broll_plan"] = "\n".join(_dedupe_clean_terms([*proof_lines, *original_broll], 10))
    upgraded["hashtags"] = _affiliate_seo_tags(product, body.platform, language)
    upgraded["quality_notes"] = [
        "Rewrote the first line as a purchase-retention hook.",
        "Added proof-based B-roll shots for product-media, Pexels, or AI generation.",
        "Strengthened title, caption, and hashtags around review/search intent.",
    ]
    return upgraded


def _affiliate_review_template(body: AffiliateReviewBody) -> Dict[str, Any]:
    language = body.target_language or "vi"
    product = body.product_name.strip()
    spoken_product = _affiliate_product_voice_name(product)
    duration = max(15, min(60, int(body.duration_seconds or 30)))
    claims, pros, cons = _infer_affiliate_points(body, language)
    audience = body.audience.strip()
    audience_text = audience or ("ngÆ°á»i Ä‘ang cÃ¢n nháº¯c sáº£n pháº©m nÃ y" if language == "vi" else "people considering this product")
    experience = body.real_experience.strip()
    disclosure = (
        "MÃ¬nh cÃ³ thá»ƒ nháº­n hoa há»“ng tá»« liÃªn káº¿t mua hÃ ng."
        if language == "vi" else
        "I may earn a commission from the purchase link."
    )
    if language == "vi":
        pro_fragment = _affiliate_fragment(pros[0])
        con_fragment = _affiliate_fragment(cons[0])
        if duration <= 15:
            narration_lines = [
                f"Khoan mua vá»™i {spoken_product}. Nghe mÃ¬nh nÃ³i tháº­t má»™t chÃºt.",
                f"MÃ¬nh Ä‘Ã£ thá»­ nhanh rá»“i: {_affiliate_expressive_line(experience)}.",
                f"Náº¿u báº¡n thuá»™c nhÃ³m {audience_text}, Ä‘Ã¢y lÃ  mÃ³n Ä‘Ã¡ng cÃ¢n nháº¯c.",
                f"NhÆ°ng nhá»› kiá»ƒm tra giÃ¡, báº£o hÃ nh vÃ  Ä‘iá»u kiá»‡n Ä‘á»•i tráº£ trÆ°á»›c khi chá»‘t Ä‘Æ¡n. {disclosure}",
            ]
        elif duration >= 60:
            narration_lines = [
                f"MÃ¬nh vá»«a dÃ¹ng thá»­ {spoken_product}. VÃ  nÃ³i tháº­t, cáº£m giÃ¡c Ä‘áº§u tiÃªn lÃ ... sáº£n pháº©m nÃ y khÃ´ng dÃ nh cho táº¥t cáº£ má»i ngÆ°á»i.",
                f"Äiá»ƒm mÃ¬nh quan tÃ¢m nháº¥t khÃ´ng pháº£i quáº£ng cÃ¡o nÃ³i gÃ¬, mÃ  lÃ  lÃºc dÃ¹ng tháº­t cÃ³ tiá»‡n khÃ´ng.",
                f"Vá»›i tráº£i nghiá»‡m cá»§a mÃ¬nh: {_affiliate_expressive_line(experience)}.",
                f"Äiá»ƒm á»•n lÃ  {pro_fragment}.",
                f"NhÆ°ng Ä‘iá»ƒm cáº§n tá»‰nh tÃ¡o lÃ  {con_fragment}.",
                f"Náº¿u báº¡n lÃ  {audience_text}, thÃ¬ Ä‘Ã¢y lÃ  mÃ³n cÃ³ thá»ƒ Ä‘Ã¡ng Ä‘á»ƒ xem thÃªm.",
                "CÃ²n náº¿u báº¡n ká»³ vá»ng má»™t sáº£n pháº©m hoÃ n háº£o ngay tá»« láº§n Ä‘áº§u dÃ¹ng, mÃ¬nh nghÄ© nÃªn cÃ¢n nháº¯c ká»¹ hÆ¡n.",
                "TrÆ°á»›c khi mua, hÃ£y xem láº¡i giÃ¡ hiá»‡n táº¡i, chÃ­nh sÃ¡ch Ä‘á»•i tráº£ vÃ  vÃ i Ä‘Ã¡nh giÃ¡ gáº§n nháº¥t.",
                f"MÃ¬nh Ä‘á»ƒ thÃ´ng tin á»Ÿ pháº§n sáº£n pháº©m Ä‘á»ƒ báº¡n tá»± kiá»ƒm tra trÆ°á»›c khi quyáº¿t Ä‘á»‹nh. {disclosure}",
            ]
        else:
            narration_lines = [
                f"MÃ¬nh vá»«a dÃ¹ng thá»­ {spoken_product}. VÃ  cÃ³ vÃ i Ä‘iá»ƒm ráº¥t Ä‘Ã¡ng nÃ³i trÆ°á»›c khi mua.",
                f"Tráº£i nghiá»‡m thá»±c táº¿ cá»§a mÃ¬nh lÃ : {_affiliate_expressive_line(experience)}.",
                f"CÃ¡i mÃ¬nh thÃ­ch lÃ  {pro_fragment}.",
                f"NhÆ°ng cÅ©ng pháº£i nÃ³i tháº­t, {con_fragment}.",
                f"Náº¿u báº¡n lÃ  {audience_text}, Ä‘Ã¢y lÃ  mÃ³n Ä‘Ã¡ng Ä‘á»ƒ xem thÃªm.",
                f"CÃ²n trÆ°á»›c khi chá»‘t Ä‘Æ¡n, nhá»› kiá»ƒm tra giÃ¡ má»›i nháº¥t vÃ  Ä‘iá»u kiá»‡n Ä‘á»•i tráº£. {disclosure}",
            ]
        visual_lines = [
            "Cáº£nh má»Ÿ Ä‘áº§u cáº§m sáº£n pháº©m trÃªn tay hoáº·c Ä‘áº·t trÃªn bÃ n, Ã¡nh sÃ¡ng rÃµ.",
            "Cáº­n cáº£nh bao bÃ¬, nhÃ£n, dung tÃ­ch vÃ  cháº¥t liá»‡u tháº­t cá»§a sáº£n pháº©m.",
            "Quay thao tÃ¡c dÃ¹ng sáº£n pháº©m trong bá»‘i cáº£nh Ä‘á»i thÆ°á»ng.",
            f"Cáº£nh minh há»a Ä‘iá»ƒm Ä‘Ã¡ng chÃº Ã½: {pros[0]}",
            f"Cáº£nh minh há»a Ä‘iá»ƒm cáº§n cÃ¢n nháº¯c: {cons[0]}",
            "Cáº£nh ngÆ°á»i dÃ¹ng so sÃ¡nh nhu cáº§u tháº­t trÆ°á»›c khi báº¥m mua.",
            "Cáº£nh káº¿t vá»›i trang sáº£n pháº©m vÃ  disclosure affiliate rÃµ rÃ ng.",
        ]
        title = f"Review nhanh {product}: cÃ³ Ä‘Ã¡ng mua khÃ´ng?"
        caption = f"Review tháº­t vá» {product}. {disclosure} #review #tiktokshop #affiliate"
        hashtags = ["review", "tiktokshop", "affiliate", "muasamthongminh", re.sub(r"\s+", "", product.lower())[:30]]
    else:
        hook = f"I tested {product}, and here is what to check before buying."
        claim_line = f"The main seller claim is {claims[0]}." if claims else f"The key thing to inspect is {pros[0]}."
        narration_lines = [
            hook, claim_line, f"My real usage note: {experience}",
            f"What I liked most: {pros[0]}.", f"What to watch out for: {cons[0]}.",
            f"It fits {audience}, but do not buy from one short video alone.",
            "Check the latest product details and price before deciding.", disclosure,
        ]
        visual_lines = [
            f"Opening shot holding or placing {product} on a clean table",
            f"Close-up of real packaging, material, and product details for {product}",
            f"Hands-on demo using {product} in a normal setting",
            f"Visual proof of the strongest benefit: {pros[0]}",
            f"Visual note showing the limitation: {cons[0]}",
            "Person comparing real needs before purchase",
            "Closing shot with product page and clear affiliate disclosure",
        ]
        title = f"Quick {product} review: worth buying?"
        caption = f"Honest review of {product}. {disclosure} #review #affiliate #shopping"
        hashtags = ["review", "affiliate", "shopping", "productreview", re.sub(r"\s+", "", product.lower())[:30]]

    if language != "vi" and duration <= 15:
        narration_lines = narration_lines[:5] + [disclosure]
        visual_lines = visual_lines[:5]
    elif language != "vi" and duration >= 60:
        visual_lines.extend([
            f"Extra close-up of daily use details for {product}",
            "Side-by-side shot of who should buy and who should skip",
        ])
    result = {
        "generator": "template",
        "model": None,
        "product_url": body.product_url,
        "title": title,
        "caption": caption,
        "hashtags": list(dict.fromkeys([tag for tag in hashtags if tag])),
        "disclosure": disclosure,
        "narration_script": "\n".join(narration_lines),
        "broll_plan": "\n".join(visual_lines),
        "compliance_warnings": _affiliate_compliance_warnings(body),
        "duration_seconds": duration,
    }
    return _affiliate_strengthen_review_output(body, result)


@app.post("/api/affiliate/review")
def affiliate_review(body: AffiliateReviewBody, user_id: int = Depends(get_current_user_id)):
    if not body.product_name.strip():
        raise HTTPException(400, "Missing product name")
    if not body.real_experience.strip():
        raise HTTPException(400, "Missing real experience note")
    if not 15 <= int(body.duration_seconds or 0) <= 60:
        raise HTTPException(400, "Affiliate review duration must be 15, 30, or 60 seconds")
    return _affiliate_product_ad_creative(body)


@app.post("/api/product-media/upload")
async def upload_product_media(file: UploadFile = File(...), user_id: int = Depends(get_current_user_id)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mkv"):
        raise HTTPException(400, "Only PNG, JPG, WEBP, MP4, MOV, WEBM, or MKV product media is supported")

    media_id = uuid.uuid4().hex[:12]
    dest = _PRODUCT_MEDIA_UPLOAD_DIR / f"{user_id}_{media_id}{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    media_type = _uploaded_product_media_kind(dest)
    return {
        "ok": True,
        "media_id": media_id,
        "media_type": media_type,
        "filename": file.filename or dest.name,
        "preview_url": f"/api/product-media/{media_id}",
    }


@app.get("/api/product-media/{media_id}")
def get_product_media(media_id: str, user_id: int = Depends(get_current_user_id)):
    path = _uploaded_product_media_path(user_id, media_id)
    if not path:
        raise HTTPException(404, "Product media not found")
    media_type = "video/mp4" if _uploaded_product_media_kind(path) == "video" else "image/jpeg"
    return FileResponse(path, media_type=media_type)


@app.post("/api/upload-logo")
async def upload_logo(file: UploadFile = File(...), user_id: int = Depends(get_current_user_id)):
    """Upload a logo/watermark image ahead of submitting a job. Returns an
    opaque id to pass back as `NewJobBody.logo_path` â€” the actual file lives
    at `{user_id}_{id}{ext}` under the server's logo-upload dir, namespaced
    by user id so nobody can reference someone else's upload."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "Chá»‰ há»— trá»£ áº£nh PNG, JPG hoáº·c WEBP")

    logo_id = uuid.uuid4().hex[:12]
    dest = _LOGO_UPLOAD_DIR / f"{user_id}_{logo_id}{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "logo_path": logo_id, "preview_url": f"/api/logo-preview/{logo_id}"}


@app.get("/api/logo-preview/{logo_id}")
def logo_preview(logo_id: str, user_id: int = Depends(get_current_user_id)):
    matches = list(_LOGO_UPLOAD_DIR.glob(f"{user_id}_{logo_id}.*"))
    if not matches:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y logo")
    return FileResponse(matches[0])


@app.get("/api/languages")
def list_languages():
    """Language options for the frontend's source/target dropdowns. Target
    languages are whatever TTS actually has a matching voice for, so this
    list can never silently drift out of sync with what actually works."""
    targets = [
        {"code": code, "label": label}
        for code, label in LANGUAGE_LABELS.items()
        if voice_for_language(code)
    ]
    sources = [{"code": "auto", "label": "Tá»± Ä‘á»™ng phÃ¡t hiá»‡n"}] + [
        {"code": code, "label": LANGUAGE_LABELS.get(code, code)}
        for code in ocr_language_map.OCR_LANGUAGE_MAP
    ]
    return {"targets": targets, "sources": sources}


@app.get("/api/voices")
def list_voices(language: str = "vi"):
    """Curated male/female voice choices for `language`, for the optional
    'chá»n giá»ng Ä‘á»c' dropdown. Empty list means: no curated options for
    this language, UI should just show 'Máº·c Ä‘á»‹nh' (None -> whatever
    tts.voice_for_language() picks automatically, unchanged behavior)."""
    voices = voices_for_language(language)
    return {
        "voices": voices,
        "language": language,
        "language_label": LANGUAGE_LABELS.get(language, language),
        "default_voice": voice_for_language(language),
    }


@app.post("/api/tts/synthesize")
async def tts_synthesize(body: TTSSynthesizeBody, user_id: int = Depends(get_current_user_id)):
    """Synthesize text to speech using EdgeTTS. Returns audio file."""
    if not body.text.strip():
        raise HTTPException(400, "Thiáº¿u vÄƒn báº£n cáº§n chuyá»ƒn Ä‘á»•i")
    
    import edge_tts
    
    # Determine voice
    preset = body.voice or voice_for_language(body.language)
    preset_parts = preset.split("|") if preset else []
    effective_voice = preset_parts[0] if preset_parts else voice_for_language(body.language)
    
    # Create temporary file for audio
    temp_dir = Path(TEMP_DIR) / "tts_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_path = temp_dir / f"tts_{uuid.uuid4().hex}.mp3"
    
    try:
        # Synthesize using EdgeTTS
        communicate = edge_tts.Communicate(
            body.text.replace("\n", " "),
            effective_voice,
            rate=body.rate,
            pitch=body.pitch,
        )
        await communicate.save(str(output_path))
        
        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError("TTS khÃ´ng táº¡o Ä‘Æ°á»£c file Ã¢m thanh há»£p lá»‡")
        
        return FileResponse(
            output_path,
            media_type="audio/mpeg",
            filename=f"tts_{uuid.uuid4().hex}.mp3",
        )
    except Exception as exc:
        logger.error("TTS synthesis error: %s", exc)
        raise HTTPException(500, f"Lá»—i khi táº¡o giá»ng nÃ³i: {str(exc)}")


@app.get("/api/presets")
def list_video_presets(user_id: int = Depends(get_current_user_id)):
    """List all video presets for the current user."""
    presets = store.list_video_presets(user_id)
    return {"presets": presets}


@app.post("/api/presets")
def create_video_preset(body: VideoPresetBody, user_id: int = Depends(get_current_user_id)):
    """Create a new video preset."""
    if not body.name.strip():
        raise HTTPException(400, "Thiáº¿u tÃªn preset")
    preset_id = store.create_video_preset(
        user_id,
        body.name,
        body.template,
        body.transition,
        body.color_effect,
        body.audio_filters,
        body.video_quality,
        body.is_default,
    )
    return {"ok": True, "preset_id": preset_id}


@app.get("/api/presets/{preset_id}")
def get_video_preset(preset_id: int, user_id: int = Depends(get_current_user_id)):
    """Get a specific video preset."""
    preset = store.get_video_preset(preset_id, user_id)
    if not preset:
        raise HTTPException(404, "Preset khÃ´ng tá»“n táº¡i")
    return preset


@app.put("/api/presets/{preset_id}")
def update_video_preset(preset_id: int, body: VideoPresetBody, user_id: int = Depends(get_current_user_id)):
    """Update a video preset."""
    success = store.update_video_preset(
        preset_id,
        user_id,
        name=body.name,
        template=body.template,
        transition=body.transition,
        color_effect=body.color_effect,
        audio_filters=body.audio_filters,
        video_quality=body.video_quality,
        is_default=body.is_default,
    )
    if not success:
        raise HTTPException(404, "Preset khÃ´ng tá»“n táº¡i")
    return {"ok": True}


@app.delete("/api/presets/{preset_id}")
def delete_video_preset(preset_id: int, user_id: int = Depends(get_current_user_id)):
    """Delete a video preset."""
    success = store.delete_video_preset(preset_id, user_id)
    if not success:
        raise HTTPException(404, "Preset khÃ´ng tá»“n táº¡i")
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs(
    q: Optional[str] = None,
    date_from: Optional[float] = None,
    date_to: Optional[float] = None,
    user_id: int = Depends(get_current_user_id),
):
    """History list for the logged-in user only. Optional `q` (matches
    title/source URL), `date_from`/`date_to` (unix timestamps) narrow it
    down â€” used by the history panel's search + date-range filter."""
    if q or date_from is not None or date_to is not None:
        jobs = store.search_jobs_for_user(user_id, query=q, date_from=date_from, date_to=date_to)
    else:
        jobs = store.list_jobs_for_user(user_id)
    return [j.to_dict() for j in jobs]


@app.get("/api/job-queue/status")
def job_queue_status(user_id: int = Depends(get_current_user_id)):
    stats = store.user_stats(user_id)
    by_status = stats.get("by_status", {})
    queued = int(by_status.get("queued", 0) or 0)
    running = int(by_status.get("running", 0) or 0)
    review = int(by_status.get("review", 0) or 0)
    try:
        import redis  # noqa: F401
        redis_client_available = True
    except ImportError:
        redis_client_available = False
    return {
        "phase": "Milestone 6",
        "backend": "RedisQueue",
        "redis_client_available": redis_client_available,
        "redis_url": _redact_redis_url(REDIS_URL),
        "web_runner": "local-thread",
        "web_runner_note": "Redis queue backend is available; existing web jobs still use the local runner until service refactor is enabled.",
        "queued": queued,
        "running": running,
        "review": review,
        "active": queued + running + review,
        "total_jobs": int(stats.get("total_jobs", 0) or 0),
    }


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    deleted = store.delete_job(job_id, user_id)
    if not deleted:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y job")
    return {"ok": True}


@app.post("/api/jobs/bulk-delete")
def bulk_delete_jobs(body: BulkDeleteBody, user_id: int = Depends(get_current_user_id)):
    if not body.job_ids:
        raise HTTPException(400, "ChÆ°a chá»n video cáº§n xoÃ¡")
    return {"ok": True, "deleted": store.delete_jobs(body.job_ids, user_id)}


def _get_owned_job(job_id: str, user_id: int):
    job = store.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y job")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    return _get_owned_job(job_id, user_id).to_dict()


@app.get("/api/jobs/{job_id}/video")
def get_job_video(job_id: str, download: bool = False, user_id: int = Depends(get_current_user_id)):
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path or not Path(job.final_video_path).exists():
        raise HTTPException(404, "Video chÆ°a sáºµn sÃ ng")
    filename = f"{job.title or job_id}.mp4".replace("/", "_")
    return FileResponse(
        job.final_video_path,
        media_type="video/mp4",
        filename=filename if download else None,
    )


@app.get("/api/jobs/{job_id}/source-video")
def get_job_source_video(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Stream the source while a review-mode job is paused.

    Read-only and scoped to the owning user; this does not alter review
    segments or start the renderer.
    """
    job = _get_owned_job(job_id, user_id)
    if job.status != "review" or not job.review_state_json:
        raise HTTPException(404, "Video nguá»“n chá»‰ cÃ³ khi job Ä‘ang chá» duyá»‡t")
    state = json.loads(job.review_state_json)
    source_path = Path(state.get("video_path", ""))
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y video nguá»“n cá»§a job")
    return FileResponse(source_path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/prepublish-check")
def get_prepublish_check(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Return a read-only technical and platform-fit report."""
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path:
        raise HTTPException(404, "Video chÆ°a sáºµn sÃ ng Ä‘á»ƒ kiá»ƒm tra")
    try:
        return prepublish_report_to_dict(inspect_for_publish(Path(job.final_video_path)))
    except FileNotFoundError as exc:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y video Ä‘áº§u ra") from exc
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/jobs/{job_id}/quality-review")
def get_quality_review(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Return actionable improvement advice for a finished video."""
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path:
        raise HTTPException(404, "Video chÃ†Â°a sÃ¡ÂºÂµn sÃƒÂ ng Ã„â€˜Ã¡Â»Æ’ review")
    segments = json.loads(job.segments_json) if job.segments_json else []
    try:
        report = review_finished_video(
            Path(job.final_video_path),
            title=job.title or "",
            source_url=job.source_url,
            target_language=job.target_language,
            segments=segments,
        )
        return video_review_report_to_dict(report)
    except FileNotFoundError as exc:
        raise HTTPException(404, "KhÃƒÂ´ng tÃƒÂ¬m thÃ¡ÂºÂ¥y video Ã„â€˜Ã¡ÂºÂ§u ra") from exc
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Re-submit a failed job with identical settings, as a brand-new job
    (the failed attempt stays in history too, for reference)."""
    old_job = _get_owned_job(job_id, user_id)
    if old_job.status != "error":
        raise HTTPException(400, "Chá»‰ cÃ³ thá»ƒ thá»­ láº¡i job bá»‹ lá»—i")

    user = store.get_user_by_id(user_id)
    if JOB_COST_CREDITS > 0 and user["credits"] < JOB_COST_CREDITS:
        raise HTTPException(402, f"KhÃ´ng Ä‘á»§ credit (cÃ²n {user['credits']}, cáº§n {JOB_COST_CREDITS})")

    new_job = store.retry_job(job_id, user_id)
    if new_job is None:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y job")
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    if old_job.source_language.startswith("creator"):
        topic = old_job.source_url.removeprefix("creator:").strip()
        image_provider = old_job.source_language.partition(":")[2] or "stock"
        retry_body = CreatorJobBody(
            topic=topic, target_language=old_job.target_language,
            image_provider=image_provider,
        )
        task = asyncio.create_task(asyncio.to_thread(_run_creator_job, new_job.id, retry_body))
    else:
        task = asyncio.create_task(_run_job(new_job.id))
    _running_tasks[new_job.id] = task
    return new_job.to_dict()


@app.get("/api/jobs/{job_id}/segments")
def get_job_segments(job_id: str, user_id: int = Depends(get_current_user_id)):
    """The translated sentences for a job that's paused at status='review'
    (or any job, really â€” useful after 'done' too, e.g. to review what was
    actually said). Powers the subtitle-editor panel."""
    job = _get_owned_job(job_id, user_id)
    segments = json.loads(job.segments_json) if job.segments_json else []
    return {"status": job.status, "segments": segments}


@app.put("/api/jobs/{job_id}/segments")
def update_job_segments(job_id: str, body: UpdateSegmentsBody, user_id: int = Depends(get_current_user_id)):
    """Save edits to the translated sentences. Only allowed while the job
    is sitting at status='review' â€” editing text that's already been
    rendered into a video wouldn't do anything, which would be confusing
    rather than harmless, so it's blocked instead of silently ignored."""
    job = _get_owned_job(job_id, user_id)
    if job.status != "review":
        raise HTTPException(400, "Job nÃ y khÃ´ng á»Ÿ tráº¡ng thÃ¡i chá» chá»‰nh sá»­a phá»¥ Ä‘á»")
    store.set_job_segments(job_id, [s.model_dump() for s in body.segments])
    return {"ok": True}


@app.post("/api/jobs/{job_id}/render")
async def render_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Continue a job paused at status='review' through to a finished
    video, using whatever's currently saved in its segments (edited or
    not)."""
    job = _get_owned_job(job_id, user_id)
    if job.status != "review":
        raise HTTPException(400, "Job nÃ y khÃ´ng á»Ÿ tráº¡ng thÃ¡i chá» render")
    task = asyncio.create_task(_run_render_from_review(job_id))
    _running_tasks[job_id] = task
    return {"ok": True}


@app.get("/api/jobs/{job_id}/subtitles.srt")
def get_job_subtitles_srt(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Export the translated subtitles as a standalone .srt â€” independent
    of the final video, e.g. for someone who wants to burn/sync them with
    other editing software instead of (or in addition to) this app's own
    render."""
    job = _get_owned_job(job_id, user_id)
    segments = json.loads(job.segments_json) if job.segments_json else []
    if not segments:
        raise HTTPException(404, "Job nÃ y chÆ°a cÃ³ phá»¥ Ä‘á» Ä‘Ã£ dá»‹ch")

    def _srt_timestamp(seconds: float) -> str:
        seconds = max(0.0, seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        millis = int(round((secs - int(secs)) * 1000))
        return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},{millis:03d}"

    lines = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{_srt_timestamp(seg['start'])} --> {_srt_timestamp(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    srt_content = "\n".join(lines)

    filename = f"{(job.title or job_id)[:60]}.srt".replace("/", "_")
    return HTMLResponse(
        content=srt_content, media_type="application/x-subrip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/jobs/{job_id}/publish")
def publish_job(job_id: str, body: PublishBody, user_id: int = Depends(get_current_user_id)):
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path or not Path(job.final_video_path).exists():
        raise HTTPException(400, "Video chÆ°a sáºµn sÃ ng Ä‘á»ƒ Ä‘Äƒng")

    results = []
    for platform in body.platforms:
        try:
            uploader = get_uploader(platform)
        except ValueError as exc:
            results.append({"platform": platform, "success": False, "message": str(exc)})
            continue

        access_token, account_ref = _resolve_publish_credentials(user_id, platform)
        result = uploader.upload(
            Path(job.final_video_path), body.title, body.description, body.hashtags,
            access_token=access_token, account_ref=account_ref,
        )
        store.log_publish(job_id, result.platform, result.success, result.message, result.remote_url)
        results.append(result.__dict__)
    return {"results": results}


@app.post("/api/jobs/{job_id}/schedule-publish")
def schedule_publish(job_id: str, body: SchedulePublishBody, user_id: int = Depends(get_current_user_id)):
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path or not Path(job.final_video_path).exists():
        raise HTTPException(400, "Video chÆ°a sáºµn sÃ ng Ä‘á»ƒ lÃªn lá»‹ch")
    allowed = {"tiktok", "facebook", "youtube"}
    if not body.platforms or any(p not in allowed for p in body.platforms):
        raise HTTPException(400, "Ná»n táº£ng khÃ´ng há»£p lá»‡")
    if body.scheduled_at < time.time() + 60:
        raise HTTPException(400, "Thá»i gian Ä‘Äƒng pháº£i muá»™n hÆ¡n hiá»‡n táº¡i Ã­t nháº¥t 1 phÃºt")
    post_id = store.create_scheduled_post(
        user_id, job_id, body.platforms, body.title, body.description,
        body.hashtags, body.scheduled_at,
    )
    return {"ok": True, "id": post_id}


@app.get("/api/scheduled-posts")
def scheduled_posts(user_id: int = Depends(get_current_user_id)):
    return [{
        "id": r["id"], "job_id": r["job_id"],
        "platforms": json.loads(r["platforms_json"]), "title": r["title"],
        "scheduled_at": r["scheduled_at"], "status": r["status"],
        "result": json.loads(r["result_json"]) if r["result_json"] else None,
    } for r in store.list_scheduled_posts(user_id)]


@app.delete("/api/scheduled-posts/{post_id}")
def cancel_scheduled_post(post_id: int, user_id: int = Depends(get_current_user_id)):
    if not store.cancel_scheduled_post(post_id, user_id):
        raise HTTPException(400, "Lá»‹ch Ä‘Äƒng khÃ´ng cÃ²n á»Ÿ tráº¡ng thÃ¡i chá»")
    return {"ok": True}


def _run_scheduled_post(row) -> None:
    job = store.get_job(row["job_id"])
    results = []
    try:
        if not job or job.user_id != row["user_id"] or not job.final_video_path or not Path(job.final_video_path).exists():
            raise RuntimeError("Video nguá»“n khÃ´ng cÃ²n tá»“n táº¡i")
        for platform in json.loads(row["platforms_json"]):
            uploader = get_uploader(platform)
            token, account_ref = _resolve_publish_credentials(row["user_id"], platform)
            result = uploader.upload(
                Path(job.final_video_path), row["title"], row["description"],
                json.loads(row["hashtags_json"]), access_token=token, account_ref=account_ref,
            )
            store.log_publish(job.id, result.platform, result.success, result.message, result.remote_url)
            results.append(result.__dict__)
        store.finish_scheduled_post(row["id"], "done" if all(r["success"] for r in results) else "error", results)
    except Exception as exc:
        logger.exception("Scheduled post %s failed", row["id"])
        store.finish_scheduled_post(row["id"], "error", {"message": str(exc)})


async def _scheduled_publish_loop() -> None:
    while True:
        for row in store.claim_due_scheduled_posts(time.time()):
            await asyncio.to_thread(_run_scheduled_post, row)
        await asyncio.sleep(15)


@app.on_event("startup")
async def start_scheduled_publish_worker() -> None:
    store.recover_processing_scheduled_posts()
    app.state.scheduled_publish_task = asyncio.create_task(_scheduled_publish_loop())


@app.on_event("shutdown")
async def stop_scheduled_publish_worker() -> None:
    task = getattr(app.state, "scheduled_publish_task", None)
    if task:
        task.cancel()


# Version info
APP_VERSION = "1.0.0"
GITHUB_REPO = "truongcongdai/Chinese_Video_Localization_AI"


@app.get("/api/version")
def get_version():
    """Get current application version."""
    return {
        "version": APP_VERSION,
        "features": {
            "animated_subtitles": True,
            "ai_script_generation": True,
            "video_templates": True,
            "queue_management": True,
            "quality_review": True,
            "improve_this_video": True,
            "content_quality_director": True,
            "voice_visual_director": True,
            "product_ad_mockup_renderer": True,
            "product_ad_model_prompt": True,
            "video_presets": True,
            "advanced_audio_filters": True,
            "openai_integration": OPENAI_AVAILABLE,
        }
    }


@app.get("/api/updates/check")
async def check_updates():
    """Check for available updates from GitHub releases."""
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=10
        )
        if response.status_code == 200:
            release = response.json()
            latest_version = release.get("tag_name", "").lstrip("v")
            current_version = APP_VERSION
            
            # Simple version comparison
            latest_parts = [int(x) for x in latest_version.split(".") if x.isdigit()]
            current_parts = [int(x) for x in current_version.split(".") if x.isdigit()]
            
            has_update = False
            if len(latest_parts) >= len(current_parts):
                for i in range(len(current_parts)):
                    if latest_parts[i] > current_parts[i]:
                        has_update = True
                        break
            
            return {
                "current_version": current_version,
                "latest_version": latest_version,
                "has_update": has_update,
                "release_notes": release.get("body", ""),
                "download_url": release.get("html_url", ""),
                "published_at": release.get("published_at", ""),
            }
        else:
            return {
                "current_version": APP_VERSION,
                "error": "Could not check for updates",
                "status_code": response.status_code
            }
    except Exception as exc:
        return {
            "current_version": APP_VERSION,
            "error": str(exc),
            "has_update": False
        }


def _resolve_publish_credentials(user_id: int, platform: str) -> tuple[Optional[str], Optional[str]]:
    """Look up the logged-in user's own connected account for this platform
    (see `/api/social/connect/*`). Shared env credentials are only considered
    when the operator explicitly enables the legacy single-account mode."""
    row = store.get_social_account(user_id, platform)
    if not row:
        if ALLOW_SHARED_SOCIAL_CREDENTIALS:
            return None, None
        raise HTTPException(
            400,
            f"Báº¡n chÆ°a káº¿t ná»‘i tÃ i khoáº£n {platform}. HÃ£y báº¥m Káº¿t ná»‘i trÆ°á»›c khi Ä‘Äƒng.",
        )
    access_token = row["access_token"]
    if platform == "youtube" and row["refresh_token"]:
        # YouTube access tokens are short-lived (~1h); always mint a fresh
        # one at publish time rather than trusting whatever was cached.
        try:
            access_token = oauth_module.GoogleOAuth().refresh_access_token(row["refresh_token"])
        except Exception:
            logger.exception("Failed to refresh YouTube token for user %s", user_id)
    return access_token, row["account_ref"]


# ------------------------------------------------------- social connect --

def _redirect_uri(request: Request, platform: str) -> str:
    return str(request.base_url).rstrip("/") + f"/api/social/callback/{platform}"


@app.get("/api/social/connections")
def list_social_connections(user_id: int = Depends(get_current_user_id)):
    rows = {r["platform"]: r for r in store.list_social_accounts(user_id)}
    out = {}
    for platform in ("tiktok", "facebook", "youtube"):
        client = oauth_module.get_oauth_client(platform)
        row = rows.get(platform)
        out[platform] = {
            "configured": client.is_configured(),
            "connected": row is not None,
            "account_name": row["account_name"] if row else None,
            "not_configured_message": None if client.is_configured() else client.not_configured_message(),
        }
    return out


@app.get("/api/social/connect/{platform}")
def connect_social(platform: str, request: Request, user_id: int = Depends(get_current_user_id)):
    try:
        client = oauth_module.get_oauth_client(platform)
    except ValueError:
        raise HTTPException(404, "Ná»n táº£ng khÃ´ng há»— trá»£")
    if not client.is_configured():
        raise HTTPException(400, client.not_configured_message())

    state = oauth_module.new_state()
    store.create_oauth_state(state, user_id, platform)
    redirect_uri = _redirect_uri(request, platform)
    authorize_url = client.authorize_url(redirect_uri, state)
    return {
        "authorize_url": authorize_url,
        "qr_code_url": oauth_module.qr_code_url(authorize_url),
    }


@app.get("/api/social/callback/{platform}")
def social_callback(platform: str, request: Request, code: str = "", state: str = "", error: str = ""):
    """
    Redirect target the OAuth provider sends the browser back to after the
    user approves (or denies) access. Not behind `get_current_user_id`
    because the browser arrives here from an external redirect without our
    session necessarily being the "current" request context in all
    browsers â€” instead, the `state` token (created per-user in
    `connect_social` above) tells us who was connecting.
    """
    close_html = (
        "<html><body style='background:#12141a;color:#eef0f3;font-family:sans-serif;"
        "display:flex;align-items:center;justify-content:center;height:100vh'>"
        "<div>{message}<script>setTimeout(()=>{{window.close();"
        "if(window.opener){{window.opener.postMessage('social-connected','*')}}"
        "}}, 1200);</script></div></body></html>"
    )
    if error:
        return HTMLResponse(close_html.format(message=f"ÄÃ£ huá»· káº¿t ná»‘i: {error}"))

    state_row = store.consume_oauth_state(state)
    if not state_row or state_row["platform"] != platform:
        return HTMLResponse(close_html.format(message="LiÃªn káº¿t Ä‘Äƒng nháº­p khÃ´ng há»£p lá»‡ hoáº·c Ä‘Ã£ háº¿t háº¡n."), status_code=400)

    user_id = state_row["user_id"]
    try:
        client = oauth_module.get_oauth_client(platform)
        redirect_uri = _redirect_uri(request, platform)
        result = client.exchange_code(code, redirect_uri)
        store.upsert_social_account(
            user_id, platform,
            access_token=result.access_token, refresh_token=result.refresh_token,
            expires_at=result.expires_at, account_name=result.account_name,
            account_ref=result.account_ref,
        )
        label = result.account_name or "tÃ i khoáº£n cá»§a báº¡n"
        return HTMLResponse(close_html.format(message=f"âœ“ ÄÃ£ káº¿t ná»‘i {platform} ({label}). CÃ³ thá»ƒ Ä‘Ã³ng cá»­a sá»• nÃ y."))
    except Exception as exc:
        logger.exception("OAuth callback failed for platform=%s", platform)
        return HTMLResponse(close_html.format(message=f"Káº¿t ná»‘i tháº¥t báº¡i: {exc}"), status_code=400)


@app.delete("/api/social/connections/{platform}")
def disconnect_social(platform: str, user_id: int = Depends(get_current_user_id)):
    store.delete_social_account(user_id, platform)
    return {"ok": True}


# ------------------------------------------------------------------ yt-dlp YouTube tools --

@app.post("/api/youtube/download")
def download_youtube_video(body: YouTubeDownloadBody, user_id: int = Depends(get_current_user_id)):
    """Download video from YouTube using yt-dlp."""
    try:
        tools = YouTubeTools(user_id)
        result = tools.download_video(body.url, body.format)
        return result
    except Exception as exc:
        logger.exception("YouTube download failed: %s", exc)
        raise HTTPException(500, str(exc))


@app.post("/api/youtube/audio")
def extract_youtube_audio(body: YouTubeDownloadBody, user_id: int = Depends(get_current_user_id)):
    """Extract audio from YouTube video."""
    try:
        tools = YouTubeTools(user_id)
        result = tools.extract_audio(body.url)
        return result
    except Exception as exc:
        logger.exception("YouTube audio extraction failed: %s", exc)
        raise HTTPException(500, str(exc))


@app.post("/api/youtube/subtitles")
def download_youtube_subtitles(body: YouTubeDownloadBody, user_id: int = Depends(get_current_user_id)):
    """Download subtitles from YouTube video."""
    try:
        tools = YouTubeTools(user_id)
        result = tools.download_subtitles(body.url)
        return result
    except Exception as exc:
        logger.exception("YouTube subtitle download failed: %s", exc)
        raise HTTPException(500, str(exc))


@app.post("/api/youtube/thumbnail")
def download_youtube_thumbnail(body: YouTubeDownloadBody, user_id: int = Depends(get_current_user_id)):
    """Download thumbnail from YouTube video."""
    try:
        tools = YouTubeTools(user_id)
        result = tools.download_thumbnail(body.url)
        return result
    except Exception as exc:
        logger.exception("YouTube thumbnail download failed: %s", exc)
        raise HTTPException(500, str(exc))


@app.post("/api/youtube/metadata", response_model=YouTubeMetadataResponse)
def get_youtube_metadata(body: YouTubeDownloadBody):
    """Extract metadata from YouTube video."""
    try:
        tools = YouTubeTools(0)  # user_id not needed for metadata
        result = tools.get_metadata(body.url)
        return YouTubeMetadataResponse(**result)
    except Exception as exc:
        logger.exception("YouTube metadata extraction failed: %s", exc)
        raise HTTPException(500, str(exc))


# ------------------------------------------------------------------ admin --

@app.get("/api/admin/users")
def admin_list_users(_admin_id: int = Depends(require_admin_user_id)):
    return [
        {"id": u["id"], "username": u["username"], "credits": u["credits"],
         "is_admin": bool(u["is_admin"]), "created_at": u["created_at"]}
        for u in store.list_users()
    ]


@app.post("/api/admin/users")
def admin_create_user(body: CreateUserBody, _admin_id: int = Depends(require_admin_user_id)):
    if store.get_user_by_username(body.username):
        raise HTTPException(409, "TÃªn Ä‘Äƒng nháº­p Ä‘Ã£ tá»“n táº¡i")
    if len(body.password) < 8:
        raise HTTPException(400, "Máº­t kháº©u cáº§n tá»‘i thiá»ƒu 8 kÃ½ tá»±")
    user_id = store.create_user_by_admin(body.username, hash_password(body.password), credits=body.credits)
    return {"ok": True, "user_id": user_id}


@app.post("/api/admin/users/{target_user_id}/credits")
def admin_adjust_credits(target_user_id: int, body: CreditsAdjustBody,
                          _admin_id: int = Depends(require_admin_user_id)):
    if not store.get_user_by_id(target_user_id):
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y user")
    if body.set_to is not None:
        store.set_credits(target_user_id, body.set_to)
    elif body.delta is not None:
        store.adjust_credits(target_user_id, body.delta)
    else:
        raise HTTPException(400, "Cáº§n truyá»n delta hoáº·c set_to")
    user = store.get_user_by_id(target_user_id)
    return {"ok": True, "credits": user["credits"]}


@app.get("/api/admin/stats")
def admin_stats(_admin_id: int = Depends(require_admin_user_id)):
    return store.admin_stats()


@app.get("/api/admin/feedback")
def admin_list_feedback(_admin_id: int = Depends(require_admin_user_id)):
    return [
        {"id": f["id"], "username": f["username"], "email": f["email"], "phone": f["phone"], "message": f["message"],
         "page": f["page"], "created_at": f["created_at"]}
        for f in store.list_feedback()
    ]


@app.get("/api/admin/top-up-requests")
def admin_list_top_up_requests(_admin_id: int = Depends(require_admin_user_id)):
    return [
        {
            "id": r["id"], "user_id": r["user_id"], "username": r["username"],
            "credits": r["credits"], "amount_vnd": r["amount_vnd"],
            "payment_method": r["payment_method"], "note": r["note"],
            "status": r["status"], "admin_note": r["admin_note"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
        for r in store.list_top_up_requests()
    ]


@app.post("/api/admin/top-up-requests/{request_id}/approve")
def admin_approve_top_up_request(
    request_id: int,
    body: TopUpDecisionBody,
    _admin_id: int = Depends(require_admin_user_id),
):
    row = store.approve_top_up_request(request_id, admin_note=(body.admin_note or "").strip() or None)
    if row is None:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y yÃªu cáº§u náº¡p Ä‘ang chá»")
    return {"ok": True, "status": row["status"]}


@app.post("/api/admin/top-up-requests/{request_id}/reject")
def admin_reject_top_up_request(
    request_id: int,
    body: TopUpDecisionBody,
    _admin_id: int = Depends(require_admin_user_id),
):
    row = store.reject_top_up_request(request_id, admin_note=(body.admin_note or "").strip() or None)
    if row is None:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y yÃªu cáº§u náº¡p Ä‘ang chá»")
    return {"ok": True, "status": row["status"]}


# ---------------------------------------------------------------- feedback --

@app.post("/api/feedback")
def submit_feedback(body: FeedbackBody, user_id: int = Depends(get_current_user_id)):
    if not body.message.strip():
        raise HTTPException(400, "Ná»™i dung gÃ³p Ã½ khÃ´ng Ä‘Æ°á»£c Ä‘á»ƒ trá»‘ng")
    store.create_feedback(user_id, body.message.strip()[:4000], page=body.page)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}

