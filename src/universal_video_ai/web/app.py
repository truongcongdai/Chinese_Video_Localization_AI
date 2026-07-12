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
import logging
import os
import re
import shutil
import traceback
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Request, status, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from universal_video_ai.orchestrator.factory import create_localization_service
from universal_video_ai.render.renderer import RenderConfig
from universal_video_ai.render import ocr_language_map
from universal_video_ai.tts.tts import DEFAULT_VOICES_BY_LANGUAGE
from universal_video_ai.config import TEMP_DIR
from universal_video_ai.social import get_uploader

from .store import Store
from .auth import (
    COOKIE_NAME, hash_password, verify_password,
    create_session_cookie_value, get_current_user_id,
)
from . import oauth as oauth_module
from . import identity_oauth

logger = logging.getLogger("universal_video_ai.web")

app = FastAPI(title="Video Localization AI")

# Same default DB as the Telegram bot (scripts/run_bot.py's --db), but the
# web app gets its own table set (users/jobs/publish_log) inside it via
# Store's schema, so both can safely share one sqlite file if you want.
_DB_PATH = Path(os.environ.get("WEB_DB_PATH", TEMP_DIR / "database.sqlite3"))
_OUTPUT_BASE_DIR = TEMP_DIR / "output"

# Credits consumed per submitted job. Purely a usage-limiting knob (there's
# no billing/payment wired up) — an admin tops up a user's balance from the
# admin dashboard. Set to 0 to disable the whole credits gate.
JOB_COST_CREDITS = int(os.environ.get("JOB_COST_CREDITS", "1"))

store = Store(_DB_PATH)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")

# Whether anyone can self-register a new account (via email/phone/Google/
# GitHub/Facebook) at any time, vs. the original "only the very first user,
# ever" model. The very first account created (by whatever method) is
# always made admin regardless of this setting. Default true: this app is
# meant to support multiple people signing up on their own now.
OPEN_REGISTRATION = os.environ.get("OPEN_REGISTRATION", "true").lower() not in ("0", "false", "no")

_LOGO_UPLOAD_DIR = TEMP_DIR / "web_uploads" / "logos"
_LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Human-readable Vietnamese labels for language codes, shown in the
# frontend's dropdowns. Target-language options come straight from
# whatever TTS actually has a voice for (DEFAULT_VOICES_BY_LANGUAGE) so the
# list never silently drifts out of sync with what actually works.
LANGUAGE_LABELS = {
    "vi": "Tiếng Việt", "en": "Tiếng Anh", "zh": "Tiếng Trung (giản thể)",
    "zh-tw": "Tiếng Trung (phồn thể)", "ja": "Tiếng Nhật", "ko": "Tiếng Hàn",
    "fr": "Tiếng Pháp", "de": "Tiếng Đức", "es": "Tiếng Tây Ban Nha",
    "pt": "Tiếng Bồ Đào Nha", "ru": "Tiếng Nga", "th": "Tiếng Thái",
    "id": "Tiếng Indonesia", "ar": "Tiếng Ả Rập", "hi": "Tiếng Hindi",
}

# In-memory guard against double-submitting the same job id concurrently;
# actual job state lives in the DB (store) so it survives restarts for
# already-finished jobs, just not for one that was mid-run at restart time.
_running_tasks: dict[str, asyncio.Task] = {}


def require_admin_user_id(user_id: int = Depends(get_current_user_id)) -> int:
    user = store.get_user_by_id(user_id)
    if not user or not user["is_admin"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Yêu cầu quyền admin")
    return user_id


# ---------------------------------------------------------------- schemas --

class LoginBody(BaseModel):
    identifier: str  # username, email, or phone number — whichever the account was created with
    password: str


class RegisterBody(BaseModel):
    identifier: str  # an email address or a phone number
    password: str


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
    # ---- Anti-copyright options ----
    enable_anti_copyright: bool = False
    letterbox: Optional[str] = None  # "left:right:top:bottom" in pixels
    zoom_factor: float = 1.0
    flip_horizontal: bool = False
    speed_factor: float = 1.0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    noise_amount: int = 0
    rotation_degrees: float = 0.0
    crop: Optional[str] = None  # "left:right:top:bottom" in pixels
    # ---- Platform-specific optimization ----
    target_platform: str = "none"  # none, tiktok, youtube_shorts, youtube_long, facebook, instagram
    target_aspect_ratio: str = "auto"  # auto, 9:16, 16:9, 1:1
    target_resolution: Optional[str] = None  # "width,height" e.g. "1080,1920"


class BatchJobBody(BaseModel):
    urls: List[str]
    target_language: str = "vi"
    source_language: str = "auto"
    logo_path: Optional[str] = None
    logo_corner: str = "bottom_right"
    logo_size_px: int = 120
    enable_anti_copyright: bool = False
    letterbox: Optional[str] = None
    zoom_factor: float = 1.0
    flip_horizontal: bool = False
    speed_factor: float = 1.0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    noise_amount: int = 0
    rotation_degrees: float = 0.0
    crop: Optional[str] = None
    target_platform: str = "none"
    target_aspect_ratio: str = "auto"
    target_resolution: Optional[str] = None


class PublishBody(BaseModel):
    platforms: List[str]
    title: str
    description: str = ""
    hashtags: List[str] = []


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
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ auth --

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?\d[\d\s.-]{7,14}\d$")


def _classify_identifier(identifier: str) -> tuple[str, str]:
    """Return (kind, normalized_value) where kind is 'email', 'phone', or
    'username' — lets one input box on the login/register form accept any
    of the three, and tells the register endpoint which DB column to use."""
    value = identifier.strip()
    if _EMAIL_RE.match(value):
        return "email", value.lower()
    digits_only = re.sub(r"[\s.-]", "", value)
    if _PHONE_RE.match(value):
        return "phone", digits_only
    return "username", value


def _unique_username_from(base: str) -> str:
    """Derive a `users.username` value (that column is UNIQUE NOT NULL) from
    an email/phone identifier, since a person registering with just an
    email never picked a separate username. Appends a numeric suffix on
    collision."""
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", base.split("@")[0]) or "user"
    candidate = base
    suffix = 0
    while store.get_user_by_username(candidate):
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def _login_response(user_id: int) -> JSONResponse:
    resp = JSONResponse({"ok": True, "user_id": user_id})
    resp.set_cookie(COOKIE_NAME, create_session_cookie_value(user_id), httponly=True, samesite="lax")
    return resp


@app.post("/api/register")
def register(body: RegisterBody):
    """
    Self-service registration via email or phone number + password.

    The very FIRST account ever created on this server (by any method —
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
            "Đăng ký đang bị khoá bởi quản trị viên. Liên hệ admin để được cấp tài khoản.",
        )
    if len(body.password) < 8:
        raise HTTPException(400, "Mật khẩu cần tối thiểu 8 ký tự")

    kind, value = _classify_identifier(body.identifier)
    if not value:
        raise HTTPException(400, "Vui lòng nhập tên đăng nhập, email hoặc số điện thoại")

    if kind == "username":
        if len(value) < 3:
            raise HTTPException(400, "Tên đăng nhập cần tối thiểu 3 ký tự")
        if store.get_user_by_username(value):
            raise HTTPException(409, "Tên đăng nhập này đã được sử dụng")
        username, email, phone = value, None, None
    else:
        existing = store.get_user_by_email(value) if kind == "email" else store.get_user_by_phone(value)
        if existing:
            raise HTTPException(409, "Email/số điện thoại này đã được đăng ký")
        username = _unique_username_from(value)
        email = value if kind == "email" else None
        phone = value if kind == "phone" else None

    user_id = store.create_user(
        username, hash_password(body.password),
        is_admin=is_first_user, credits=10_000 if is_first_user else 10,
        email=email, phone=phone,
    )
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
    user = store.get_user_by_identifier(body.identifier.strip())
    if not user or not user["password_hash"] or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Sai thông tin đăng nhập hoặc mật khẩu")
    return _login_response(user["id"])


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/api/me")
def me(user_id: int = Depends(get_current_user_id)):
    user = store.get_user_by_id(user_id)
    return {
        "id": user["id"], "username": user["username"],
        "email": user["email"], "phone": user["phone"],
        "credits": user["credits"], "is_admin": bool(user["is_admin"]),
    }


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
        raise HTTPException(404, "Nhà cung cấp đăng nhập không được hỗ trợ")
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
    sets the session cookie, then redirects back to the app's home page —
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
        # in with Google/etc. for the first time — link it to the same
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

async def _run_job(job_id: str) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Đang tải video...")

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

        render_config = None
        if job.logo_path and Path(job.logo_path).exists() or job.enable_anti_copyright or job.target_platform != "none":
            # Parse target_resolution if provided
            target_resolution = None
            if job.target_resolution:
                try:
                    w, h = job.target_resolution.split(",")
                    target_resolution = (int(w.strip()), int(h.strip()))
                except:
                    self.logger.warning("Invalid target_resolution format: %s", job.target_resolution)

            render_config = RenderConfig(
                logo_path=job.logo_path,
                logo_corner=job.logo_corner or "bottom_right",
                logo_size_px=job.logo_size_px or 120,
                letterbox=job.letterbox,
                zoom_factor=job.zoom_factor,
                flip_horizontal=job.flip_horizontal,
                speed_factor=job.speed_factor,
                brightness=job.brightness,
                contrast=job.contrast,
                saturation=job.saturation,
                noise_amount=job.noise_amount,
                rotation_degrees=job.rotation_degrees,
                crop=job.crop,
                target_platform=job.target_platform,
                target_aspect_ratio=job.target_aspect_ratio,
                target_resolution=target_resolution,
            )

        service = create_localization_service(
            run_transcription=True,
            transcription_language=transcription_language,
            run_translation=True,
            target_language=job.target_language,
            run_tts=True,
            generate_subtitles=True,
            mix_audio=True,
            render_video=True,
            render_config=render_config,
            enable_text_cover=True,
            ocr_languages=ocr_languages,
            logger=logger,
        )
        job_output_dir = _OUTPUT_BASE_DIR / "web_jobs" / job_id
        store.update_job(job_id, progress_note="Đang xử lý (dịch, lồng tiếng, render)...")
        result = await service.localize(job.source_url, job_output_dir, target_language=job.target_language)

        if result.final_video_path and Path(result.final_video_path).exists():
            title = (result.translated_text or job.source_url)[:80]
            store.update_job(
                job_id, status="done", progress_note="Hoàn tất",
                final_video_path=str(result.final_video_path), title=title,
            )
        else:
            store.update_job(job_id, status="error", error="Không tạo được video đầu ra (final_video_path rỗng)")
            _refund_job_credits(job)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        store.update_job(job_id, status="error", error=f"{exc}\n{traceback.format_exc()[-1500:]}")
        _refund_job_credits(job)
    finally:
        _running_tasks.pop(job_id, None)


def _refund_job_credits(job) -> None:
    """A job that errors out shouldn't cost the user credit — refund it."""
    if JOB_COST_CREDITS > 0:
        try:
            store.adjust_credits(job.user_id, JOB_COST_CREDITS)
        except Exception:
            logger.exception("Failed to refund credits for job %s", job.id)


@app.post("/api/jobs")
async def create_job(body: NewJobBody, user_id: int = Depends(get_current_user_id)):
    if not body.url.strip():
        raise HTTPException(400, "Thiếu đường link video")

    user = store.get_user_by_id(user_id)
    if JOB_COST_CREDITS > 0 and user["credits"] < JOB_COST_CREDITS:
        raise HTTPException(
            402,
            f"Không đủ credit (còn {user['credits']}, cần {JOB_COST_CREDITS}). "
            "Liên hệ admin để được cấp thêm.",
        )

    logo_path = None
    if body.logo_path:
        # body.logo_path is actually the opaque id POST /api/upload-logo
        # returned — resolve it back to a real file path scoped to this
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
        enable_anti_copyright=body.enable_anti_copyright,
        letterbox=body.letterbox,
        zoom_factor=body.zoom_factor,
        flip_horizontal=body.flip_horizontal,
        speed_factor=body.speed_factor,
        brightness=body.brightness,
        contrast=body.contrast,
        saturation=body.saturation,
        noise_amount=body.noise_amount,
        rotation_degrees=body.rotation_degrees,
        crop=body.crop,
        target_platform=body.target_platform,
        target_aspect_ratio=body.target_aspect_ratio,
        target_resolution=body.target_resolution,
    )
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    task = asyncio.create_task(_run_job(job.id))
    _running_tasks[job.id] = task
    return job.to_dict()


@app.post("/api/jobs/batch")
async def create_batch_jobs(body: BatchJobBody, user_id: int = Depends(get_current_user_id)):
    """Create multiple jobs at once with the same configuration."""
    if not body.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    # Check credits for all jobs
    total_credits_needed = len(body.urls) * JOB_COST_CREDITS
    user = store.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user["credits"] < total_credits_needed:
        raise HTTPException(
            status_code=402,
            detail=f"Not enough credits. Need {total_credits_needed}, have {user['credits']}"
        )

    # Resolve logo path if provided
    logo_path = None
    if body.logo_path:
        logo_path = body.logo_path
        # Try to find in logos directory
        logos_dir = _OUTPUT_BASE_DIR / "logos"
        candidate = logos_dir / body.logo_path
        if candidate.exists():
            logo_path = str(candidate)
        elif Path(body.logo_path).exists():
            logo_path = str(body.logo_path)

    # Parse target_resolution if provided
    target_resolution = None
    if body.target_resolution:
        try:
            w, h = body.target_resolution.split(",")
            target_resolution = (int(w.strip()), int(h.strip()))
        except:
            logger.warning("Invalid target_resolution format: %s", body.target_resolution)

    # Create jobs
    created_jobs = []
    for url in body.urls:
        url = url.strip()
        if not url:
            continue

        job = store.create_job(
            user_id, url, body.target_language,
            source_language=body.source_language or "auto",
            logo_path=logo_path,
            logo_corner=body.logo_corner or "bottom_right",
            logo_size_px=body.logo_size_px or 120,
            enable_anti_copyright=body.enable_anti_copyright,
            letterbox=body.letterbox,
            zoom_factor=body.zoom_factor,
            flip_horizontal=body.flip_horizontal,
            speed_factor=body.speed_factor,
            brightness=body.brightness,
            contrast=body.contrast,
            saturation=body.saturation,
            noise_amount=body.noise_amount,
            rotation_degrees=body.rotation_degrees,
            crop=body.crop,
            target_platform=body.target_platform,
            target_aspect_ratio=body.target_aspect_ratio,
            target_resolution=target_resolution,
        )
        created_jobs.append(job)

        # Start job processing
        task = asyncio.create_task(_run_job(job.id))
        _running_tasks[job.id] = task

    # Deduct credits
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -total_credits_needed)

    return {"jobs": [j.to_dict() for j in created_jobs], "total": len(created_jobs)}


@app.post("/api/upload-logo")
async def upload_logo(file: UploadFile = File(...), user_id: int = Depends(get_current_user_id)):
    """Upload a logo/watermark image ahead of submitting a job. Returns an
    opaque id to pass back as `NewJobBody.logo_path` — the actual file lives
    at `{user_id}_{id}{ext}` under the server's logo-upload dir, namespaced
    by user id so nobody can reference someone else's upload."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "Chỉ hỗ trợ ảnh PNG, JPG hoặc WEBP")

    logo_id = uuid.uuid4().hex[:12]
    dest = _LOGO_UPLOAD_DIR / f"{user_id}_{logo_id}{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "logo_path": logo_id, "preview_url": f"/api/logo-preview/{logo_id}"}


@app.get("/api/logo-preview/{logo_id}")
def logo_preview(logo_id: str, user_id: int = Depends(get_current_user_id)):
    matches = list(_LOGO_UPLOAD_DIR.glob(f"{user_id}_{logo_id}.*"))
    if not matches:
        raise HTTPException(404, "Không tìm thấy logo")
    return FileResponse(matches[0])


@app.get("/api/languages")
def list_languages():
    """Language options for the frontend's source/target dropdowns. Target
    languages are whatever TTS actually has a matching voice for, so this
    list can never silently drift out of sync with what actually works."""
    targets = [
        {"code": code, "label": LANGUAGE_LABELS.get(code, code)}
        for code in DEFAULT_VOICES_BY_LANGUAGE
    ]
    sources = [{"code": "auto", "label": "Tự động phát hiện"}] + [
        {"code": code, "label": LANGUAGE_LABELS.get(code, code)}
        for code in ocr_language_map.OCR_LANGUAGE_MAP
    ]
    return {"targets": targets, "sources": sources}


@app.get("/api/jobs")
def list_jobs(
    q: Optional[str] = None,
    date_from: Optional[float] = None,
    date_to: Optional[float] = None,
    user_id: int = Depends(get_current_user_id),
):
    """History list for the logged-in user only. Optional `q` (matches
    title/source URL), `date_from`/`date_to` (unix timestamps) narrow it
    down — used by the history panel's search + date-range filter."""
    if q or date_from is not None or date_to is not None:
        jobs = store.search_jobs_for_user(user_id, query=q, date_from=date_from, date_to=date_to)
    else:
        jobs = store.list_jobs_for_user(user_id)
    return [j.to_dict() for j in jobs]


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    deleted = store.delete_job(job_id, user_id)
    if not deleted:
        raise HTTPException(404, "Không tìm thấy job")
    return {"ok": True}


def _get_owned_job(job_id: str, user_id: int):
    job = store.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(404, "Không tìm thấy job")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    return _get_owned_job(job_id, user_id).to_dict()


@app.get("/api/jobs/{job_id}/video")
def get_job_video(job_id: str, download: bool = False, user_id: int = Depends(get_current_user_id)):
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path or not Path(job.final_video_path).exists():
        raise HTTPException(404, "Video chưa sẵn sàng")
    filename = f"{job.title or job_id}.mp4".replace("/", "_")
    return FileResponse(
        job.final_video_path,
        media_type="video/mp4",
        filename=filename if download else None,
    )


@app.post("/api/jobs/{job_id}/publish")
def publish_job(job_id: str, body: PublishBody, user_id: int = Depends(get_current_user_id)):
    job = _get_owned_job(job_id, user_id)
    if not job.final_video_path or not Path(job.final_video_path).exists():
        raise HTTPException(400, "Video chưa sẵn sàng để đăng")

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


def _resolve_publish_credentials(user_id: int, platform: str) -> tuple[Optional[str], Optional[str]]:
    """Look up the logged-in user's own connected account for this platform
    (see `/api/social/connect/*`). Falls back to no override (letting the
    uploader use its shared env-var credentials, if any admin configured
    those instead) when the user hasn't connected this platform themselves."""
    row = store.get_social_account(user_id, platform)
    if not row:
        return None, None
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
        raise HTTPException(404, "Nền tảng không hỗ trợ")
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
    browsers — instead, the `state` token (created per-user in
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
        return HTMLResponse(close_html.format(message=f"Đã huỷ kết nối: {error}"))

    state_row = store.consume_oauth_state(state)
    if not state_row or state_row["platform"] != platform:
        return HTMLResponse(close_html.format(message="Liên kết đăng nhập không hợp lệ hoặc đã hết hạn."), status_code=400)

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
        label = result.account_name or "tài khoản của bạn"
        return HTMLResponse(close_html.format(message=f"✓ Đã kết nối {platform} ({label}). Có thể đóng cửa sổ này."))
    except Exception as exc:
        logger.exception("OAuth callback failed for platform=%s", platform)
        return HTMLResponse(close_html.format(message=f"Kết nối thất bại: {exc}"), status_code=400)


@app.delete("/api/social/connections/{platform}")
def disconnect_social(platform: str, user_id: int = Depends(get_current_user_id)):
    store.delete_social_account(user_id, platform)
    return {"ok": True}


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
        raise HTTPException(409, "Tên đăng nhập đã tồn tại")
    if len(body.password) < 8:
        raise HTTPException(400, "Mật khẩu cần tối thiểu 8 ký tự")
    user_id = store.create_user_by_admin(body.username, hash_password(body.password), credits=body.credits)
    return {"ok": True, "user_id": user_id}


@app.post("/api/admin/users/{target_user_id}/credits")
def admin_adjust_credits(target_user_id: int, body: CreditsAdjustBody,
                          _admin_id: int = Depends(require_admin_user_id)):
    if not store.get_user_by_id(target_user_id):
        raise HTTPException(404, "Không tìm thấy user")
    if body.set_to is not None:
        store.set_credits(target_user_id, body.set_to)
    elif body.delta is not None:
        store.adjust_credits(target_user_id, body.delta)
    else:
        raise HTTPException(400, "Cần truyền delta hoặc set_to")
    user = store.get_user_by_id(target_user_id)
    return {"ok": True, "credits": user["credits"]}


@app.get("/api/admin/stats")
def admin_stats(_admin_id: int = Depends(require_admin_user_id)):
    return store.admin_stats()


@app.get("/health")
def health():
    return {"status": "ok"}
