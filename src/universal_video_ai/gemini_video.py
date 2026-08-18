"""Shared Gemini video generation used by Content OS and quick creator jobs."""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


logger = logging.getLogger(__name__)

_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
_FILES_URL = "https://generativelanguage.googleapis.com/v1beta/files"


class GeminiVideoGenerationError(RuntimeError):
    """Raised when neither Gemini Omni nor Veo returns a usable video."""


def _response_error(response: Any, model: str) -> GeminiVideoGenerationError:
    status_code = int(getattr(response, "status_code", 0) or 0)
    body = str(getattr(response, "text", "") or "").strip().replace("\n", " ")[:600]
    if status_code == 429:
        reason = "hết quota hoặc đang bị giới hạn tần suất (HTTP 429)"
        if model == "gemini-omni-flash-preview" or model.startswith("veo-"):
            reason += "; video generation cần Gemini API paid tier/billing hoạt động"
    else:
        reason = f"HTTP {status_code}" if status_code else "HTTP request thất bại"
    suffix = f": {body}" if body else ""
    return GeminiVideoGenerationError(f"Gemini model {model} {reason}{suffix}")


def _find_video_output(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return the first inline video data or file URI from REST response variants."""
    stack: list[Any] = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            mime_type = str(value.get("mime_type") or value.get("mimeType") or "").lower()
            output_type = str(value.get("type") or "").lower()
            looks_like_video = output_type == "video" or mime_type.startswith("video/")
            if looks_like_video and value.get("data"):
                return str(value["data"]), None
            if looks_like_video and value.get("uri"):
                return None, str(value["uri"])
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None, None


def _download_file_uri(
    uri: str,
    output_path: Path,
    api_key: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> bool:
    headers = {"x-goog-api-key": api_key}
    file_match = re.search(r"/files/([^/:?]+)", uri)
    if not file_match:
        raise GeminiVideoGenerationError(f"Gemini trả về URI video không hợp lệ: {uri[:200]}")
    file_id = file_match.group(1)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        metadata = requests.get(f"{_FILES_URL}/{file_id}", headers=headers, timeout=30)
        if metadata.ok:
            state = str(metadata.json().get("state") or "ACTIVE").upper()
            if state.endswith("FAILED"):
                return False
            if state.endswith("ACTIVE"):
                break
        time.sleep(poll_seconds)
    else:
        return False

    response = requests.get(
        f"{_FILES_URL}/{file_id}:download",
        headers=headers,
        params={"alt": "media"},
        timeout=min(300, max(60, timeout_seconds)),
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path.exists() and output_path.stat().st_size > 4096


def _generate_with_omni(
    output_path: Path,
    prompt: str,
    api_key: str,
    model: str,
    aspect_ratio: str,
    timeout_seconds: float,
    poll_seconds: float,
    duration_seconds: int,
    reference_images: Optional[List[Path]] = None,
) -> bool:
    references = []
    for image_path in (reference_images or [])[:3]:
        path = Path(image_path)
        if not path.is_file():
            continue
        mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        if not mime_type.startswith("image/"):
            continue
        references.append({
            "type": "image",
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "mime_type": mime_type,
        })
    interaction_input: Any = [*references, {"type": "text", "text": prompt}] if references else prompt
    request_payload = {
        "model": model,
        "input": interaction_input,
        "response_format": {
            "type": "video",
            "aspect_ratio": aspect_ratio,
            "duration": f"{max(3, min(10, int(duration_seconds)))}s",
            "delivery": os.getenv("GEMINI_VIDEO_DELIVERY") or "uri",
        },
        "generation_config": {
            "video_config": {
                "task": "reference_to_video" if references else "text_to_video",
            }
        },
    }
    max_retries = max(0, int(os.getenv("GEMINI_VIDEO_MAX_RETRIES") or "2"))
    response = None
    for attempt in range(max_retries + 1):
        response = requests.post(
            _INTERACTIONS_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=request_payload,
            timeout=timeout_seconds,
        )
        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code != 429:
            break
        response_text = str(getattr(response, "text", "") or "").lower()
        if "free_tier_requests" in response_text or "limit: 0" in response_text:
            raise _response_error(response, model)
        if attempt >= max_retries:
            raise _response_error(response, model)
        headers = getattr(response, "headers", {}) or {}
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        try:
            delay = max(1.0, min(60.0, float(retry_after))) if retry_after else min(30.0, 3.0 * (2 ** attempt))
        except (TypeError, ValueError):
            delay = min(30.0, 3.0 * (2 ** attempt))
        logger.warning(
            "Gemini video model %s returned 429; retrying in %.1fs (%s/%s)",
            model, delay, attempt + 1, max_retries,
        )
        time.sleep(delay)

    if response is None:
        raise GeminiVideoGenerationError(f"Gemini model {model} không trả về phản hồi")
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        raise _response_error(response, model)
    response.raise_for_status()
    inline_data, uri = _find_video_output(response.json())
    if inline_data:
        output_path.write_bytes(base64.b64decode(inline_data))
        return output_path.exists() and output_path.stat().st_size > 4096
    if uri:
        return _download_file_uri(
            uri, output_path, api_key,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
    logger.warning("Gemini Omni response contained no video output")
    return False


def _generate_with_veo(
    output_path: Path,
    prompt: str,
    api_key: str,
    model: str,
    aspect_ratio: str,
    duration_seconds: int,
    timeout_seconds: float,
    poll_seconds: float,
) -> bool:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=max(1, int(timeout_seconds * 1000))),
    )
    config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio,
        resolution=os.getenv("GEMINI_VIDEO_RESOLUTION") or "720p",
        duration_seconds=duration_seconds,
        number_of_videos=1,
        negative_prompt="readable text, subtitles, watermark, logo, unrelated objects, static poster",
    )
    try:
        operation = client.models.generate_videos(
            model=model,
            source=types.GenerateVideosSource(prompt=prompt),
            config=config,
        )
    except TypeError:
        operation = client.models.generate_videos(model=model, prompt=prompt, config=config)

    deadline = time.time() + timeout_seconds
    while not getattr(operation, "done", False) and time.time() < deadline:
        time.sleep(poll_seconds)
        operation = client.operations.get(operation)
    if not getattr(operation, "done", False):
        return False
    generated = getattr(getattr(operation, "response", None), "generated_videos", None) or []
    if not generated:
        return False
    video = getattr(generated[0], "video", generated[0])
    downloaded = client.files.download(file=video)
    if isinstance(downloaded, (bytes, bytearray)):
        output_path.write_bytes(downloaded)
    elif hasattr(video, "save"):
        video.save(str(output_path))
    elif getattr(video, "video_bytes", None):
        output_path.write_bytes(video.video_bytes)
    return output_path.exists() and output_path.stat().st_size > 4096


def generate_gemini_video(
    output_path: Path,
    prompt: str,
    api_key: str,
    *,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 8,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    poll_seconds: Optional[float] = None,
    reference_images: Optional[List[Path]] = None,
    raise_on_error: bool = False,
) -> bool:
    """Generate and save an MP4, preferring Gemini Omni and falling back to Veo."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_model = (model or os.getenv("GEMINI_VIDEO_MODEL") or "gemini-omni-flash-preview").strip()
    timeout = timeout_seconds or float(os.getenv("GEMINI_VIDEO_TIMEOUT_SECONDS") or "900")
    poll = poll_seconds or float(os.getenv("GEMINI_VIDEO_POLL_SECONDS") or "5")
    errors: list[str] = []
    try:
        if selected_model.startswith("gemini-"):
            if _generate_with_omni(
                output_path, prompt, api_key, selected_model, aspect_ratio, timeout, poll,
                duration_seconds, reference_images,
            ):
                return True
        else:
            if _generate_with_veo(
                output_path, prompt, api_key, selected_model, aspect_ratio,
                duration_seconds, timeout, poll,
            ):
                return True
    except Exception as exc:
        logger.warning("Primary Gemini video model %s failed: %s", selected_model, exc)
        errors.append(str(exc))
    else:
        errors.append(f"Gemini model {selected_model} không trả về video hợp lệ")

    permanent_billing_error = any(
        marker in error.lower()
        for error in errors
        for marker in ("free_tier_requests", "limit: 0")
    )
    if permanent_billing_error:
        if raise_on_error:
            raise GeminiVideoGenerationError("; ".join(errors))
        return False

    fallback_model = (os.getenv("GEMINI_VEO_FALLBACK_MODEL") or "veo-3.1-generate-preview").strip()
    if selected_model == fallback_model:
        if raise_on_error:
            raise GeminiVideoGenerationError("; ".join(errors))
        return False
    try:
        if _generate_with_veo(
            output_path, prompt, api_key, fallback_model, aspect_ratio,
            duration_seconds, timeout, poll,
        ):
            return True
        errors.append(f"Veo model {fallback_model} không trả về video hợp lệ")
    except Exception as exc:
        logger.warning("Gemini/Veo fallback %s failed: %s", fallback_model, exc)
        errors.append(str(exc))
    if raise_on_error:
        raise GeminiVideoGenerationError("; ".join(error for error in errors if error))
    return False
