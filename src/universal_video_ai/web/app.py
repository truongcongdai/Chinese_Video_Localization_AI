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
import traceback
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests

from fastapi import FastAPI, Depends, HTTPException, Request, status, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from universal_video_ai.orchestrator.factory import create_localization_service
from universal_video_ai.orchestrator.service import (
    prepared_localization_to_dict, prepared_localization_from_dict,
)
from universal_video_ai.render.renderer import RenderConfig
from universal_video_ai.render import ocr_language_map
from universal_video_ai.render.quality_check import analyze_output_quality
from universal_video_ai.tts.tts import DEFAULT_VOICES_BY_LANGUAGE
from universal_video_ai.tts.voices import voices_for_language
from universal_video_ai.segment import TranscriptSegment
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
WEB_RENDER_PRESET = os.environ.get("WEB_RENDER_PRESET", "fast")
WEB_RENDER_TIMEOUT_SECONDS = int(os.environ.get("WEB_RENDER_TIMEOUT_SECONDS", "1800"))

store = Store(_DB_PATH)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")

# Whether anyone can self-register a new account (via email/phone/Google/
# GitHub/Facebook) at any time, vs. the original "only the very first user,
# ever" model. The very first account created (by whatever method) is
# always made admin regardless of this setting. Default true: this app is
# meant to support multiple people signing up on their own now.
OPEN_REGISTRATION = os.environ.get("OPEN_REGISTRATION", "true").lower() not in ("0", "false", "no")

# Credits granted to BOTH the new user and whoever invited them, when
# registering with a valid ?ref= referral code. Set to 0 to disable bonuses
# while keeping the referral tracking itself.
REFERRAL_BONUS_CREDITS = int(os.environ.get("REFERRAL_BONUS_CREDITS", "20"))

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
    # "vi-VN-NamMinhNeural" — None uses the target language's default voice.
    tts_voice: Optional[str] = None
    # When True, the job stops right after translation (status="review")
    # instead of rendering straight through, so the person can edit the
    # translated text first via PUT .../segments then POST .../render.
    review_before_render: bool = False


class CreatorJobBody(BaseModel):
    topic: str
    script: Optional[str] = None
    target_language: str = "vi"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30


class CreatorSuggestionBody(BaseModel):
    topic: str
    target_language: str = "vi"


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


class TopUpDecisionBody(BaseModel):
    admin_note: Optional[str] = None


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

    referrer = None
    if body.referral_code and body.referral_code.strip():
        referrer = store.get_user_by_referral_code(body.referral_code.strip())
        if referrer is None:
            raise HTTPException(400, "Mã giới thiệu không hợp lệ")

    user_id = store.create_user(
        username, hash_password(body.password),
        is_admin=is_first_user, credits=10_000 if is_first_user else 10,
        email=email, phone=phone,
        referred_by_user_id=referrer["id"] if referrer else None,
    )
    if referrer is not None:
        # Both sides get a bonus — the invitee starts with extra credit
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
    referral_code = user["referral_code"] or store.ensure_referral_code(user_id)
    return {
        "id": user["id"], "username": user["username"],
        "email": user["email"], "phone": user["phone"],
        "credits": user["credits"], "is_admin": bool(user["is_admin"]),
        "referral_code": referral_code,
    }


@app.get("/api/stats/me")
def stats_me(user_id: int = Depends(get_current_user_id)):
    """Personal usage stats for the logged-in user — powers the small
    stats widget above their own history (not the admin-only site-wide
    stats at /api/admin/stats)."""
    return store.user_stats(user_id)


@app.post("/api/top-up-requests")
def create_top_up_request(body: TopUpRequestBody, user_id: int = Depends(get_current_user_id)):
    if body.credits <= 0 or body.amount_vnd <= 0:
        raise HTTPException(400, "Gói nạp không hợp lệ")
    if body.credits > 1_000_000 or body.amount_vnd > 1_000_000_000:
        raise HTTPException(400, "Gói nạp vượt giới hạn")
    request_id = store.create_top_up_request(
        user_id,
        body.credits,
        body.amount_vnd,
        body.payment_method.strip()[:40] or "bank_transfer",
        note=(body.note or "").strip()[:1000] or None,
    )
    return {"ok": True, "id": request_id}


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

def _build_service_for_job(job):
    """Shared service-construction logic for both the normal (straight-
    through) job path and the resume-after-review render path — both need
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
    if job.logo_path and Path(job.logo_path).exists():
        render_config = RenderConfig(
            preset=WEB_RENDER_PRESET,
            timeout_seconds=WEB_RENDER_TIMEOUT_SECONDS,
            logo_path=job.logo_path,
            logo_corner=job.logo_corner or "bottom_right",
            logo_size_px=job.logo_size_px or 120,
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
        render_video=True,
        render_config=render_config,
        enable_text_cover=True,
        ocr_languages=ocr_languages,
        logger=logger,
    )


async def _run_job(job_id: str) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Đang tải video...")
        service = _build_service_for_job(job)
        job_output_dir = _OUTPUT_BASE_DIR / "web_jobs" / job_id

        if job.review_mode:
            # Stop after translation and wait for the person to review/edit
            # the translated sentences via PUT .../segments, then
            # POST .../render (-> _run_render_from_review) to continue.
            store.update_job(job_id, progress_note="Đang dịch phụ đề để bạn xem trước...")
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
                progress_note="Đã dịch xong — chỉnh sửa phụ đề rồi bấm Render",
            )
            return

        store.update_job(job_id, progress_note="Đang xử lý (dịch, lồng tiếng, render)...")
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
            job_id, status="done", progress_note="Hoàn tất",
            final_video_path=str(result.final_video_path), title=title,
        )
        # Best-effort automated sanity check (quiet audio, wrong duration).
        # Never fails the job over this — it's an informational warning
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
        store.update_job(job_id, status="error", error="Không tạo được video đầu ra (final_video_path rỗng)")
        _refund_job_credits(job)


async def _run_render_from_review(job_id: str) -> None:
    """Resume a job sitting at status='review': re-hydrate what
    prepare_for_review() produced, and render using whatever's currently in
    segments_json (the person's edits, if they made any — otherwise still
    the original machine translation, unedited)."""
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Đang lồng tiếng và render...")
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
    """A job that errors out shouldn't cost the user credit — refund it."""
    if JOB_COST_CREDITS > 0:
        try:
            store.adjust_credits(job.user_id, JOB_COST_CREDITS)
        except Exception:
            logger.exception("Failed to refund credits for job %s", job.id)


def _creator_scene_brief_from_topic(topic: str, language: str) -> List[str]:
    topic = topic.strip()
    if language == "vi":
        return [
            f"Người trẻ đang học tập hoặc làm việc với laptop, không khí tập trung, chủ đề {topic}",
            "Cận cảnh màn hình máy tính, tài liệu, email, slide thuyết trình và công việc văn phòng bận rộn",
            "Không gian làm việc hiện đại với nhiều ứng dụng AI, ghi chú, biểu đồ và ý tưởng nội dung",
            "Sinh viên nghiên cứu tài liệu, tìm kiếm thông tin, đọc nguồn tham khảo trên laptop",
            "Người sáng tạo nội dung đang dựng video ngắn, viết caption, lập kế hoạch đăng mạng xã hội",
            "Giao diện thuyết trình hoặc slide đẹp, bố cục rõ ràng, cảm giác chuyên nghiệp",
            "Micro, tai nghe hoặc studio nhỏ tượng trưng cho giọng đọc AI và sản xuất nội dung",
            "Tài liệu PDF, sách, ghi chú học tập, người đặt câu hỏi và tổng hợp kiến thức",
            "Người dùng hoàn thành công việc nhanh hơn, cảm giác năng suất và nhẹ nhõm",
            "Cảnh kết thúc với điện thoại hiển thị video social media, kêu gọi bình luận và theo dõi",
        ]
    return [
        f"Young professional or student using a laptop, focused workspace, topic {topic}",
        "Close-up of computer screen, documents, email, presentation slides, busy office tasks",
        "Modern desk with AI apps, notes, charts, and content ideas",
        "Student researching information, reading sources, studying on a laptop",
        "Content creator editing short videos, writing captions, planning social posts",
        "Clean presentation slides, professional layout, modern business visuals",
        "Microphone, headphones, or small studio representing AI voice and content production",
        "PDF documents, books, notes, question answering and knowledge management",
        "Person finishing work faster, productive and relaxed mood",
        "Phone showing a social media video, comment and follow call to action",
    ]


def _creator_script_text_from_topic(topic: str, language: str) -> str:
    return "\n".join(_creator_scene_brief_from_topic(topic, language))


def _creator_keywords_from_topic(topic: str, language: str) -> List[str]:
    text = topic.lower()
    words = [
        w for w in re.sub(r"[^\w\sÀ-ỹ]", " ", text, flags=re.UNICODE).split()
        if len(w) >= 3
    ]
    stopwords = {
        "của", "cho", "với", "một", "những", "các", "trong", "bạn", "này",
        "the", "and", "for", "with", "from", "that", "this", "your",
    }
    core = []
    for word in words:
        if word not in stopwords and word not in core:
            core.append(word)
    if language == "vi":
        base = [
            topic,
            f"{topic} cho người mới",
            f"cách dùng {topic}",
            f"mẹo {topic}",
            f"{topic} miễn phí",
            f"{topic} hiệu quả",
            "công cụ AI",
            "năng suất làm việc",
            "học tập với AI",
            "tạo nội dung bằng AI",
        ]
    else:
        base = [
            topic,
            f"{topic} for beginners",
            f"how to use {topic}",
            f"{topic} tips",
            f"free {topic}",
            f"best {topic} tools",
            "AI tools",
            "productivity",
            "AI for learning",
            "AI content creation",
        ]
    for word in core[:8]:
        if word not in base:
            base.append(word)
    return base[:14]


def _creator_stock_query(topic: str, scene: str, index: int) -> str:
    generic = [
        "student laptop productivity artificial intelligence",
        "busy office computer email documents",
        "modern workspace laptop technology apps",
        "student research laptop studying library",
        "content creator editing video social media",
        "business presentation slides office",
        "microphone headphones studio voice recording",
        "books documents notes studying desk",
        "productive worker laptop success",
        "smartphone social media video vertical",
    ]
    topic_words = " ".join(re.findall(r"[A-Za-z0-9]+", topic))[:60]
    if topic_words:
        return f"{generic[index % len(generic)]} {topic_words}"
    return generic[index % len(generic)]


def _split_creator_script(topic: str, script: Optional[str], language: str) -> List[str]:
    raw = (script or "").strip()
    if not raw:
        return _creator_scene_brief_from_topic(topic, language)
    lines = [line.strip(" -\t") for line in raw.splitlines() if line.strip()]
    if len(lines) <= 1:
        lines = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", raw) if s.strip()]
    return lines[:12] or _creator_scene_brief_from_topic(topic, language)


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
    pexels_key = os.environ.get("PEXELS_API_KEY")
    pixabay_key = os.environ.get("PIXABAY_API_KEY")

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
    with requests.get(media["url"], stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with output.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    return output


def _render_stock_clip(
    source_path: Path,
    source_type: str,
    output_path: Path,
    width: int,
    height: int,
    duration: float,
) -> None:
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p"
    if source_type == "image":
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-t", f"{duration:.3f}",
            "-i", str(source_path), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", WEB_RENDER_PRESET, "-crf", "23",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-t", f"{duration:.3f}",
            "-i", str(source_path), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", WEB_RENDER_PRESET, "-crf", "23",
            str(output_path),
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "FFmpeg stock clip render failed")


def _run_creator_job(job_id: str, body: CreatorJobBody) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        store.update_job(job_id, status="running", progress_note="Đang dựng video từ ý tưởng...")
        output_dir = _OUTPUT_BASE_DIR / "web_jobs" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = _split_creator_script(body.topic, body.script, body.target_language)
        total_duration = max(10, min(180, int(body.duration_seconds or 30)))
        scene_duration = max(2.5, total_duration / len(scenes))
        if body.aspect_ratio == "16:9":
            width, height = 1920, 1080
            font_size = 58
            box_y = "(h-text_h)/2"
        else:
            width, height = 1080, 1920
            font_size = 54
            box_y = "h*0.58"

        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        colors = ["0f1115", "17202a", "10241f", "251c2b", "1f2430", "202416"]
        clip_paths: List[Path] = []
        for idx, scene in enumerate(scenes):
            clip_path = output_dir / f"scene_{idx:02d}.mp4"
            media = _search_stock_media(_creator_stock_query(body.topic, scene, idx), body.aspect_ratio)
            if media:
                try:
                    source_path = _download_stock_media(media, output_dir / f"stock_{idx:02d}")
                    _render_stock_clip(source_path, media["type"], clip_path, width, height, scene_duration)
                    store.update_job(
                        job_id,
                        progress_note=f"Đã lấy cảnh {idx + 1}/{len(scenes)} từ {media['provider']}...",
                    )
                except Exception:
                    logger.exception("Stock media render failed for scene=%s", scene)
                    media = None

            if not media:
                text_path = output_dir / f"scene_{idx:02d}.txt"
                text_path.write_text(_wrap_creator_text(scene), encoding="utf-8")
                color = colors[idx % len(colors)]
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=0x{color}:s={width}x{height}:r=30:d={scene_duration:.3f}",
                    "-vf",
                    (
                        "format=yuv420p,"
                        f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.03:t=fill,"
                        f"drawtext=fontfile={font}:textfile={text_path}:"
                        f"x=(w-text_w)/2:y={box_y}:fontsize={font_size}:"
                        "fontcolor=white:line_spacing=14:box=1:boxcolor=black@0.38:boxborderw=28"
                    ),
                    "-an", "-c:v", "libx264", "-preset", WEB_RENDER_PRESET,
                    "-crf", "24", str(clip_path),
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr or result.stdout or "FFmpeg scene render failed")
            clip_paths.append(clip_path)
            store.update_job(job_id, progress_note=f"Đã dựng {idx + 1}/{len(scenes)} cảnh...")

        concat_file = output_dir / "clips.txt"
        concat_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in clip_paths),
            encoding="utf-8",
        )
        output_path = output_dir / "output_generated.mp4"
        final_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={total_duration}",
            "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
            str(output_path),
        ]
        result = subprocess.run(final_cmd, capture_output=True, text=True, check=False, timeout=WEB_RENDER_TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "FFmpeg final render failed")
        store.update_job(
            job_id, status="done", progress_note="Hoàn tất",
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
        tts_voice=body.tts_voice or None,
        review_mode=body.review_before_render,
    )
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    task = asyncio.create_task(_run_job(job.id))
    _running_tasks[job.id] = task
    return job.to_dict()


@app.post("/api/creator/jobs")
async def create_creator_job(body: CreatorJobBody, user_id: int = Depends(get_current_user_id)):
    if not body.topic.strip():
        raise HTTPException(400, "Thiếu chủ đề video")
    if body.aspect_ratio not in ("9:16", "16:9"):
        raise HTTPException(400, "Tỷ lệ khung hình không hợp lệ")
    user = store.get_user_by_id(user_id)
    if JOB_COST_CREDITS > 0 and user["credits"] < JOB_COST_CREDITS:
        raise HTTPException(402, f"Không đủ credit (còn {user['credits']}, cần {JOB_COST_CREDITS})")

    job = store.create_job(
        user_id,
        f"creator:{body.topic.strip()}",
        body.target_language,
        source_language="creator",
    )
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    task = asyncio.create_task(asyncio.to_thread(_run_creator_job, job.id, body))
    _running_tasks[job.id] = task
    return job.to_dict()


@app.post("/api/creator/suggestions")
def creator_suggestions(body: CreatorSuggestionBody, user_id: int = Depends(get_current_user_id)):
    if not body.topic.strip():
        raise HTTPException(400, "Thiếu chủ đề video")
    topic = body.topic.strip()
    language = body.target_language or "vi"
    return {
        "keywords": _creator_keywords_from_topic(topic, language),
        "visual_brief": _creator_script_text_from_topic(topic, language),
        "script": _creator_script_text_from_topic(topic, language),
    }


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


@app.get("/api/voices")
def list_voices(language: str = "vi"):
    """Curated male/female voice choices for `language`, for the optional
    'chọn giọng đọc' dropdown. Empty list means: no curated options for
    this language, UI should just show 'Mặc định' (None -> whatever
    tts.voice_for_language() picks automatically, unchanged behavior)."""
    return {"voices": voices_for_language(language)}


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


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Re-submit a failed job with identical settings, as a brand-new job
    (the failed attempt stays in history too, for reference)."""
    old_job = _get_owned_job(job_id, user_id)
    if old_job.status != "error":
        raise HTTPException(400, "Chỉ có thể thử lại job bị lỗi")

    user = store.get_user_by_id(user_id)
    if JOB_COST_CREDITS > 0 and user["credits"] < JOB_COST_CREDITS:
        raise HTTPException(402, f"Không đủ credit (còn {user['credits']}, cần {JOB_COST_CREDITS})")

    new_job = store.retry_job(job_id, user_id)
    if new_job is None:
        raise HTTPException(404, "Không tìm thấy job")
    if JOB_COST_CREDITS > 0:
        store.adjust_credits(user_id, -JOB_COST_CREDITS)
    task = asyncio.create_task(_run_job(new_job.id))
    _running_tasks[new_job.id] = task
    return new_job.to_dict()


@app.get("/api/jobs/{job_id}/segments")
def get_job_segments(job_id: str, user_id: int = Depends(get_current_user_id)):
    """The translated sentences for a job that's paused at status='review'
    (or any job, really — useful after 'done' too, e.g. to review what was
    actually said). Powers the subtitle-editor panel."""
    job = _get_owned_job(job_id, user_id)
    segments = json.loads(job.segments_json) if job.segments_json else []
    return {"status": job.status, "segments": segments}


@app.put("/api/jobs/{job_id}/segments")
def update_job_segments(job_id: str, body: UpdateSegmentsBody, user_id: int = Depends(get_current_user_id)):
    """Save edits to the translated sentences. Only allowed while the job
    is sitting at status='review' — editing text that's already been
    rendered into a video wouldn't do anything, which would be confusing
    rather than harmless, so it's blocked instead of silently ignored."""
    job = _get_owned_job(job_id, user_id)
    if job.status != "review":
        raise HTTPException(400, "Job này không ở trạng thái chờ chỉnh sửa phụ đề")
    store.set_job_segments(job_id, [s.model_dump() for s in body.segments])
    return {"ok": True}


@app.post("/api/jobs/{job_id}/render")
async def render_job(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Continue a job paused at status='review' through to a finished
    video, using whatever's currently saved in its segments (edited or
    not)."""
    job = _get_owned_job(job_id, user_id)
    if job.status != "review":
        raise HTTPException(400, "Job này không ở trạng thái chờ render")
    task = asyncio.create_task(_run_render_from_review(job_id))
    _running_tasks[job_id] = task
    return {"ok": True}


@app.get("/api/jobs/{job_id}/subtitles.srt")
def get_job_subtitles_srt(job_id: str, user_id: int = Depends(get_current_user_id)):
    """Export the translated subtitles as a standalone .srt — independent
    of the final video, e.g. for someone who wants to burn/sync them with
    other editing software instead of (or in addition to) this app's own
    render."""
    job = _get_owned_job(job_id, user_id)
    segments = json.loads(job.segments_json) if job.segments_json else []
    if not segments:
        raise HTTPException(404, "Job này chưa có phụ đề đã dịch")

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


@app.get("/api/admin/feedback")
def admin_list_feedback(_admin_id: int = Depends(require_admin_user_id)):
    return [
        {"id": f["id"], "username": f["username"], "message": f["message"],
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
        raise HTTPException(404, "Không tìm thấy yêu cầu nạp đang chờ")
    return {"ok": True, "status": row["status"]}


@app.post("/api/admin/top-up-requests/{request_id}/reject")
def admin_reject_top_up_request(
    request_id: int,
    body: TopUpDecisionBody,
    _admin_id: int = Depends(require_admin_user_id),
):
    row = store.reject_top_up_request(request_id, admin_note=(body.admin_note or "").strip() or None)
    if row is None:
        raise HTTPException(404, "Không tìm thấy yêu cầu nạp đang chờ")
    return {"ok": True, "status": row["status"]}


# ---------------------------------------------------------------- feedback --

@app.post("/api/feedback")
def submit_feedback(body: FeedbackBody, user_id: int = Depends(get_current_user_id)):
    if not body.message.strip():
        raise HTTPException(400, "Nội dung góp ý không được để trống")
    store.create_feedback(user_id, body.message.strip()[:4000], page=body.page)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}
