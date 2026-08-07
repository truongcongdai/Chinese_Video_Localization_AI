from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import textwrap
import time
import urllib.request
import urllib.parse
import ipaddress
import socket

from .llm import PublishingLLMClient
from .models import PublishingPackConfig
from .profiles import get_channel_profile

_logger = logging.getLogger(__name__)

def _resolve_font_path(configured: Optional[str] = None) -> Optional[str]:
    candidates = [
        configured,
        os.getenv("PUBLISHING_FONT_PATH"),
        os.getenv("BRANDING_FONT_PATH"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser().resolve())
    return None


@dataclass(frozen=True)
class PublishingPackResult:
    pack_dir: Path
    manifest_path: Path
    youtube_metadata_path: Optional[Path]
    facebook_metadata_path: Optional[Path]
    publish_ready_video_path: Optional[Path]
    recommended_title: str
    warnings: List[str]
    overall_status: str = "success"  # success | partial | failed
    component_status_path: Optional[Path] = None


_PUBLISHING_COMPONENTS = (
    "analysis",
    "youtube_metadata",
    "facebook_metadata",
    "youtube_thumbnails",
    "facebook_thumbnails",
    "publish_ready",
)


def _component_enabled(config: PublishingPackConfig, component: str) -> bool:
    if component == "analysis":
        return True
    if component == "youtube_metadata":
        return "youtube" in config.platforms
    if component == "facebook_metadata":
        return "facebook" in config.platforms
    if component == "youtube_thumbnails":
        return bool(config.generate_thumbnails and "youtube" in config.platforms)
    if component == "facebook_thumbnails":
        return bool(config.generate_thumbnails and "facebook" in config.platforms)
    if component == "publish_ready":
        return bool(config.generate_publish_ready_video)
    return False


def _new_component_state(config: PublishingPackConfig) -> Dict[str, Any]:
    now = time.time()
    components: Dict[str, Any] = {}
    for name in _PUBLISHING_COMPONENTS:
        enabled = _component_enabled(config, name)
        components[name] = {
            "status": "pending" if enabled else "skipped",
            "attempts": 0,
            "error": None,
            "updated_at": now,
        }
    return {
        "schema_version": 1,
        "overall_status": "pending",
        "updated_at": now,
        "components": components,
    }


def _overall_component_status(state: Dict[str, Any]) -> str:
    components = state.get("components") or {}
    active = [
        str(item.get("status") or "pending")
        for item in components.values()
        if str(item.get("status") or "pending") != "skipped"
    ]
    if not active:
        return "success"
    if any(value == "running" for value in active):
        return "running"
    if any(value == "pending" for value in active):
        return "pending"
    successes = sum(value == "success" for value in active)
    failures = sum(value == "failed" for value in active)
    if failures == 0:
        return "success"
    if successes > 0:
        return "partial"
    return "failed"


def _component_status_path(pack_dir: Path) -> Path:
    return Path(pack_dir) / "component_status.json"


def _load_component_state(pack_dir: Path, config: PublishingPackConfig) -> Dict[str, Any]:
    path = _component_status_path(pack_dir)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("components"), dict):
                # Forward-compatible migration: make newly added components visible.
                template = _new_component_state(config)
                for name, value in template["components"].items():
                    loaded["components"].setdefault(name, value)
                loaded["overall_status"] = _overall_component_status(loaded)
                return loaded
        except Exception:
            pass
    return _new_component_state(config)


def _save_component_state(pack_dir: Path, state: Dict[str, Any]) -> Path:
    state["updated_at"] = time.time()
    state["overall_status"] = _overall_component_status(state)
    path = _component_status_path(pack_dir)
    _write_json(path, state)
    return path


def _set_component_state(
    pack_dir: Path,
    state: Dict[str, Any],
    component: str,
    status: str,
    *,
    error: Optional[str] = None,
    increment_attempt: bool = False,
) -> None:
    entry = state.setdefault("components", {}).setdefault(
        component,
        {"status": "pending", "attempts": 0, "error": None, "updated_at": time.time()},
    )
    if increment_attempt:
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["status"] = status
    entry["error"] = (str(error)[:2000] if error else None)
    entry["updated_at"] = time.time()
    _save_component_state(pack_dir, state)


def _read_pack_json(pack_dir: Path, name: str) -> Dict[str, Any]:
    path = Path(pack_dir) / name
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _component_warning(component: str, exc: Exception) -> str:
    labels = {
        "analysis": "phân tích nội dung",
        "youtube_metadata": "metadata YouTube",
        "facebook_metadata": "metadata Facebook",
        "youtube_thumbnails": "thumbnail YouTube",
        "facebook_thumbnails": "thumbnail Facebook",
        "publish_ready": "publish_ready.mp4",
    }
    return f"Không tạo được {labels.get(component, component)}: {exc}"


class PublishingPackService:
    """Create and selectively regenerate assets for a Reup Publishing Pack.

    The localized ``final.mp4`` is treated as immutable input. Every publishing
    component is best-effort and records its own status. A failed thumbnail or
    metadata component therefore never invalidates a completed Reup video.
    """

    def __init__(self, llm_client: Optional[PublishingLLMClient] = None, logger=None):
        self.llm_client = llm_client
        self.logger = logger or _logger

    def _build_payload(
        self,
        *,
        config: PublishingPackConfig,
        profile: Dict[str, Any],
        source_metadata: Dict[str, Any],
        translated_segments: Sequence[Dict[str, Any]],
        source_segments: Sequence[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], bool]:
        translated_text = "\n".join(item["text"] for item in translated_segments).strip()
        source_text = "\n".join(item["text"] for item in source_segments).strip()
        fallback = _deterministic_pack(
            profile=profile,
            config=config,
            source_metadata=source_metadata,
            translated_segments=translated_segments,
            translated_text=translated_text,
            source_text=source_text,
        )
        ai_payload = None
        if self.llm_client and self.llm_client.available:
            prompt = _publishing_prompt(
                profile=profile,
                config=config,
                source_metadata=source_metadata,
                translated_text=translated_text,
                source_text=source_text,
            )
            ai_payload = self.llm_client.generate_json(prompt)
        return _merge_and_normalize_pack(fallback, ai_payload, profile, config), bool(ai_payload)

    def _generate_platform_thumbnails(
        self,
        *,
        platform: str,
        config: PublishingPackConfig,
        pack_dir: Path,
        final_video_path: Path,
        analysis: Dict[str, Any],
        platform_metadata: Dict[str, Any],
        profile: Dict[str, Any],
        translated_segments: Sequence[Dict[str, Any]],
    ) -> List[str]:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("FFmpeg không khả dụng")
        duration = _probe_duration(final_video_path)
        timestamps = _thumbnail_timestamps(
            translated_segments,
            analysis.get("strongest_hook", ""),
            duration,
            config.thumbnail_count,
        )
        texts = _thumbnail_phrases(
            [
                analysis.get("strongest_hook", ""),
                analysis.get("main_conflict", ""),
                analysis.get("payoff", ""),
            ],
            profile,
        )
        if not texts:
            texts = [str(platform_metadata.get("recommended_title") or config.channel_name)]
        width, height = ((1280, 720) if platform == "youtube" else (1200, 630))
        prefix = f"thumbnail_{platform}_"
        temp_outputs: list[tuple[Path, Path]] = []
        names: list[str] = []
        try:
            for index, timestamp in enumerate(timestamps, start=1):
                name = f"{prefix}{index:02d}.jpg"
                final_path = pack_dir / name
                temp_path = pack_dir / f".{Path(name).stem}.retry.jpg"
                temp_path.unlink(missing_ok=True)
                overlay_text = texts[index - 1] if index - 1 < len(texts) else texts[0]
                _render_thumbnail(
                    final_video_path,
                    temp_path,
                    timestamp=timestamp,
                    text=overlay_text,
                    width=width,
                    height=height,
                )
                temp_outputs.append((temp_path, final_path))
                names.append(name)
            if len(names) != config.thumbnail_count:
                raise RuntimeError(f"chỉ tạo được {len(names)}/{config.thumbnail_count} thumbnail")
            # Replace the old set only after the new set is complete.
            for old in pack_dir.glob(f"{prefix}*.jpg"):
                old.unlink(missing_ok=True)
            for temp_path, final_path in temp_outputs:
                os.replace(temp_path, final_path)
            return names
        except Exception:
            for temp_path, _ in temp_outputs:
                temp_path.unlink(missing_ok=True)
            raise

    def _generate_publish_ready(
        self,
        *,
        config: PublishingPackConfig,
        profile: Dict[str, Any],
        pack_dir: Path,
        final_video_path: Path,
        hooks: Sequence[str],
    ) -> Path:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("FFmpeg không khả dụng")
        final_output = pack_dir / "publish_ready.mp4"
        temp_output = pack_dir / ".publish_ready.retry.mp4"
        temp_output.unlink(missing_ok=True)
        duration = _probe_duration(final_video_path)
        try:
            _render_publish_ready_video(
                final_video_path,
                temp_output,
                hook=str((list(hooks) or [""])[0] or ""),
                cta=f"Theo dõi {profile['channel_name']} để xem tập tiếp theo",
                duration=duration,
                edit_level=config.edit_level,
            )
            os.replace(temp_output, final_output)
            return final_output
        except Exception:
            temp_output.unlink(missing_ok=True)
            raise

    def _write_supporting_files(
        self,
        *,
        pack_dir: Path,
        payload: Dict[str, Any],
        profile: Dict[str, Any],
    ) -> None:
        analysis = payload.get("content_analysis") or {}
        youtube = payload.get("youtube") or {}
        facebook = payload.get("facebook") or {}
        _write_json(pack_dir / "keywords.json", {
            "primary": analysis.get("primary_keyword"),
            "secondary": analysis.get("secondary_keywords", []),
            "tags": youtube.get("tags", []),
        })
        _write_json(pack_dir / "hashtags.json", {
            "youtube": youtube.get("hashtags", []),
            "facebook": facebook.get("hashtags", []),
        })
        _write_json(pack_dir / "hook_variants.json", {"hooks": payload.get("hook_variants", [])})
        _write_json(pack_dir / "content_edit_plan.json", payload.get("content_edit_plan", {}))
        _write_json(pack_dir / "originality_report.json", payload.get("originality_report", {}))
        _write_text(pack_dir / "enhanced_hook_script.txt", "\n".join(payload.get("hook_variants", [])))
        _write_text(pack_dir / "publishing_pack.md", _markdown_pack(payload, profile))

    def _write_manifest(
        self,
        *,
        pack_dir: Path,
        config: PublishingPackConfig,
        final_video_path: Path,
        source_url: str,
        job_id: str,
        recommended_title: str,
        warnings: Sequence[str],
        state: Dict[str, Any],
    ) -> Path:
        manifest = {
            "schema_version": 2,
            "job_id": job_id,
            "source_url": source_url,
            "config": config.to_dict(),
            "recommended_title": recommended_title,
            "files": sorted(item.name for item in pack_dir.iterdir() if item.is_file()),
            "source_video_sha256": _sha256(final_video_path),
            "warnings": list(warnings),
            "overall_status": _overall_component_status(state),
            "components": state.get("components", {}),
        }
        manifest_path = pack_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        return manifest_path

    def generate(
        self,
        *,
        config: PublishingPackConfig,
        output_dir: Path,
        final_video_path: Path,
        source_video_path: Optional[Path],
        source_url: str,
        source_metadata: Dict[str, Any],
        translated_segments: Sequence[Dict[str, Any]],
        source_segments: Sequence[Dict[str, Any]],
        user_id: int,
        job_id: str,
        target_language: str,
    ) -> PublishingPackResult:
        config = config.normalized()
        if not config.enabled:
            raise ValueError("Publishing Pack is disabled")
        final_video_path = Path(final_video_path)
        if not final_video_path.is_file():
            raise FileNotFoundError(final_video_path)

        pack_dir = Path(output_dir) / "publishing_pack"
        if pack_dir.exists():
            shutil.rmtree(pack_dir)
        pack_dir.mkdir(parents=True, exist_ok=True)
        state = _new_component_state(config)
        component_status_path = _save_component_state(pack_dir, state)
        warnings: list[str] = []
        profile = get_channel_profile(config.channel_profile, config.channel_name)
        source_metadata = _clean_source_metadata(source_metadata, source_url, source_video_path)
        translated_segments = _clean_segments(translated_segments)
        source_segments = _clean_segments(source_segments)

        source_thumbnail = _download_source_thumbnail(
            source_metadata.get("thumbnail_url", ""), pack_dir, logger=self.logger,
        )
        if source_thumbnail:
            source_metadata["source_thumbnail_file"] = source_thumbnail.name
        _write_json(pack_dir / "source_metadata.json", source_metadata)

        payload: Dict[str, Any] = {}
        llm_used = False
        _set_component_state(pack_dir, state, "analysis", "running", increment_attempt=True)
        try:
            payload, llm_used = self._build_payload(
                config=config,
                profile=profile,
                source_metadata=source_metadata,
                translated_segments=translated_segments,
                source_segments=source_segments,
            )
            _write_json(pack_dir / "content_analysis.json", payload.get("content_analysis", {}))
            _set_component_state(pack_dir, state, "analysis", "success")
        except Exception as exc:
            warnings.append(_component_warning("analysis", exc))
            _set_component_state(pack_dir, state, "analysis", "failed", error=str(exc))
            # Analysis is the foundation for the other generated assets. Keep
            # a retryable pack directory instead of failing the completed Reup.
            for name in _PUBLISHING_COMPONENTS[1:]:
                if state["components"][name]["status"] == "pending":
                    _set_component_state(pack_dir, state, name, "failed", error="Phụ thuộc vào phân tích nội dung")
            manifest_path = self._write_manifest(
                pack_dir=pack_dir, config=config, final_video_path=final_video_path,
                source_url=source_url, job_id=job_id, recommended_title="",
                warnings=warnings, state=state,
            )
            return PublishingPackResult(
                pack_dir=pack_dir, manifest_path=manifest_path,
                youtube_metadata_path=None, facebook_metadata_path=None,
                publish_ready_video_path=None, recommended_title="",
                warnings=warnings, overall_status="failed",
                component_status_path=component_status_path,
            )

        youtube = dict(payload.get("youtube") or {})
        facebook = dict(payload.get("facebook") or {})

        if _component_enabled(config, "youtube_metadata"):
            _set_component_state(pack_dir, state, "youtube_metadata", "running", increment_attempt=True)
            try:
                youtube.setdefault("thumbnails", [])
                _write_json(pack_dir / "youtube_metadata.json", youtube)
                _set_component_state(pack_dir, state, "youtube_metadata", "success")
            except Exception as exc:
                warnings.append(_component_warning("youtube_metadata", exc))
                _set_component_state(pack_dir, state, "youtube_metadata", "failed", error=str(exc))

        if _component_enabled(config, "facebook_metadata"):
            _set_component_state(pack_dir, state, "facebook_metadata", "running", increment_attempt=True)
            try:
                facebook.setdefault("thumbnails", [])
                _write_json(pack_dir / "facebook_metadata.json", facebook)
                _set_component_state(pack_dir, state, "facebook_metadata", "success")
            except Exception as exc:
                warnings.append(_component_warning("facebook_metadata", exc))
                _set_component_state(pack_dir, state, "facebook_metadata", "failed", error=str(exc))

        if _component_enabled(config, "youtube_thumbnails"):
            _set_component_state(pack_dir, state, "youtube_thumbnails", "running", increment_attempt=True)
            try:
                names = self._generate_platform_thumbnails(
                    platform="youtube", config=config, pack_dir=pack_dir,
                    final_video_path=final_video_path, analysis=payload.get("content_analysis", {}),
                    platform_metadata=youtube, profile=profile,
                    translated_segments=translated_segments,
                )
                youtube["thumbnails"] = names
                _write_json(pack_dir / "youtube_metadata.json", youtube)
                _set_component_state(pack_dir, state, "youtube_thumbnails", "success")
            except Exception as exc:
                warnings.append(_component_warning("youtube_thumbnails", exc))
                _set_component_state(pack_dir, state, "youtube_thumbnails", "failed", error=str(exc))

        if _component_enabled(config, "facebook_thumbnails"):
            _set_component_state(pack_dir, state, "facebook_thumbnails", "running", increment_attempt=True)
            try:
                names = self._generate_platform_thumbnails(
                    platform="facebook", config=config, pack_dir=pack_dir,
                    final_video_path=final_video_path, analysis=payload.get("content_analysis", {}),
                    platform_metadata=facebook, profile=profile,
                    translated_segments=translated_segments,
                )
                facebook["thumbnails"] = names
                _write_json(pack_dir / "facebook_metadata.json", facebook)
                _set_component_state(pack_dir, state, "facebook_thumbnails", "success")
            except Exception as exc:
                warnings.append(_component_warning("facebook_thumbnails", exc))
                _set_component_state(pack_dir, state, "facebook_thumbnails", "failed", error=str(exc))

        publish_ready_video_path: Optional[Path] = None
        if _component_enabled(config, "publish_ready"):
            _set_component_state(pack_dir, state, "publish_ready", "running", increment_attempt=True)
            try:
                publish_ready_video_path = self._generate_publish_ready(
                    config=config, profile=profile, pack_dir=pack_dir,
                    final_video_path=final_video_path,
                    hooks=payload.get("hook_variants", []),
                )
                _set_component_state(pack_dir, state, "publish_ready", "success")
            except Exception as exc:
                warnings.append(_component_warning("publish_ready", exc))
                _set_component_state(pack_dir, state, "publish_ready", "failed", error=str(exc))

        payload["youtube"] = youtube
        payload["facebook"] = facebook
        payload["source_metadata"] = source_metadata
        payload["generation"] = {
            "generated_at": time.time(),
            "job_id": job_id,
            "user_id": user_id,
            "target_language": target_language,
            "channel_profile": config.channel_profile,
            "llm_used": llm_used,
            "publish_ready_video": publish_ready_video_path.name if publish_ready_video_path else None,
        }
        payload["warnings"] = warnings
        self._write_supporting_files(pack_dir=pack_dir, payload=payload, profile=profile)
        manifest_path = self._write_manifest(
            pack_dir=pack_dir, config=config, final_video_path=final_video_path,
            source_url=source_url, job_id=job_id,
            recommended_title=str(youtube.get("recommended_title") or facebook.get("recommended_title") or ""),
            warnings=warnings, state=state,
        )
        overall = _overall_component_status(state)
        return PublishingPackResult(
            pack_dir=pack_dir,
            manifest_path=manifest_path,
            youtube_metadata_path=(pack_dir / "youtube_metadata.json") if (pack_dir / "youtube_metadata.json").is_file() else None,
            facebook_metadata_path=(pack_dir / "facebook_metadata.json") if (pack_dir / "facebook_metadata.json").is_file() else None,
            publish_ready_video_path=publish_ready_video_path,
            recommended_title=str(youtube.get("recommended_title") or facebook.get("recommended_title") or ""),
            warnings=warnings,
            overall_status=overall,
            component_status_path=component_status_path,
        )

    def retry_components(
        self,
        *,
        component: str,
        config: PublishingPackConfig,
        output_dir: Path,
        final_video_path: Path,
        source_video_path: Optional[Path],
        source_url: str,
        source_metadata: Dict[str, Any],
        translated_segments: Sequence[Dict[str, Any]],
        source_segments: Sequence[Dict[str, Any]],
        user_id: int,
        job_id: str,
        target_language: str,
    ) -> PublishingPackResult:
        """Regenerate selected Publishing Pack components without re-running Reup."""
        config = config.normalized()
        final_video_path = Path(final_video_path)
        if not final_video_path.is_file():
            raise FileNotFoundError(final_video_path)
        pack_dir = Path(output_dir) / "publishing_pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        state = _load_component_state(pack_dir, config)
        profile = get_channel_profile(config.channel_profile, config.channel_name)
        source_metadata = _clean_source_metadata(source_metadata, source_url, source_video_path)
        existing_source = _read_pack_json(pack_dir, "source_metadata.json")
        if existing_source:
            # Prefer richer source metadata collected during the original download.
            source_metadata = {**source_metadata, **existing_source}
        _write_json(pack_dir / "source_metadata.json", source_metadata)
        translated_segments = _clean_segments(translated_segments)
        source_segments = _clean_segments(source_segments)

        requested = str(component or "failed").strip().lower()
        if requested in {"failed", "all_failed"}:
            targets = [
                name for name in _PUBLISHING_COMPONENTS
                if state.get("components", {}).get(name, {}).get("status") == "failed"
                and _component_enabled(config, name)
            ]
        elif requested == "all":
            targets = [name for name in _PUBLISHING_COMPONENTS if _component_enabled(config, name)]
        elif requested in _PUBLISHING_COMPONENTS:
            if not _component_enabled(config, requested):
                raise ValueError(f"Component {requested} đang bị tắt trong cấu hình job")
            targets = [requested]
        else:
            raise ValueError(f"Publishing component không hợp lệ: {component}")

        warnings: list[str] = []
        fresh_payload: Optional[Dict[str, Any]] = None
        llm_used = False

        def get_fresh_payload() -> Dict[str, Any]:
            nonlocal fresh_payload, llm_used
            if fresh_payload is None:
                fresh_payload, llm_used = self._build_payload(
                    config=config, profile=profile, source_metadata=source_metadata,
                    translated_segments=translated_segments, source_segments=source_segments,
                )
            return fresh_payload

        for name in targets:
            _set_component_state(pack_dir, state, name, "running", increment_attempt=True)
            try:
                if name == "analysis":
                    fresh = get_fresh_payload()
                    _write_json(pack_dir / "content_analysis.json", fresh.get("content_analysis", {}))
                    _write_json(pack_dir / "content_edit_plan.json", fresh.get("content_edit_plan", {}))
                    _write_json(pack_dir / "originality_report.json", fresh.get("originality_report", {}))
                    _write_json(pack_dir / "hook_variants.json", {"hooks": fresh.get("hook_variants", [])})
                    _write_text(pack_dir / "enhanced_hook_script.txt", "\n".join(fresh.get("hook_variants", [])))
                elif name == "youtube_metadata":
                    fresh = get_fresh_payload()
                    metadata = dict(fresh.get("youtube") or {})
                    old = _read_pack_json(pack_dir, "youtube_metadata.json")
                    metadata["thumbnails"] = old.get("thumbnails", [])
                    _write_json(pack_dir / "youtube_metadata.json", metadata)
                elif name == "facebook_metadata":
                    fresh = get_fresh_payload()
                    metadata = dict(fresh.get("facebook") or {})
                    old = _read_pack_json(pack_dir, "facebook_metadata.json")
                    metadata["thumbnails"] = old.get("thumbnails", [])
                    _write_json(pack_dir / "facebook_metadata.json", metadata)
                elif name in {"youtube_thumbnails", "facebook_thumbnails"}:
                    platform = "youtube" if name == "youtube_thumbnails" else "facebook"
                    analysis = _read_pack_json(pack_dir, "content_analysis.json")
                    if not analysis:
                        analysis = get_fresh_payload().get("content_analysis", {})
                    metadata = _read_pack_json(pack_dir, f"{platform}_metadata.json")
                    if not metadata:
                        metadata = dict(get_fresh_payload().get(platform) or {})
                    names = self._generate_platform_thumbnails(
                        platform=platform, config=config, pack_dir=pack_dir,
                        final_video_path=final_video_path, analysis=analysis,
                        platform_metadata=metadata, profile=profile,
                        translated_segments=translated_segments,
                    )
                    metadata["thumbnails"] = names
                    _write_json(pack_dir / f"{platform}_metadata.json", metadata)
                elif name == "publish_ready":
                    hooks_data = _read_pack_json(pack_dir, "hook_variants.json")
                    hooks = hooks_data.get("hooks") or []
                    if not hooks:
                        hooks = get_fresh_payload().get("hook_variants", [])
                    self._generate_publish_ready(
                        config=config, profile=profile, pack_dir=pack_dir,
                        final_video_path=final_video_path, hooks=hooks,
                    )
                _set_component_state(pack_dir, state, name, "success")
            except Exception as exc:
                warning = _component_warning(name, exc)
                warnings.append(warning)
                self.logger.warning("Publishing Pack retry failed for %s/%s: %s", job_id, name, exc)
                _set_component_state(pack_dir, state, name, "failed", error=str(exc))

        analysis = _read_pack_json(pack_dir, "content_analysis.json")
        youtube = _read_pack_json(pack_dir, "youtube_metadata.json")
        facebook = _read_pack_json(pack_dir, "facebook_metadata.json")
        hooks_data = _read_pack_json(pack_dir, "hook_variants.json")
        edit_plan = _read_pack_json(pack_dir, "content_edit_plan.json")
        originality = _read_pack_json(pack_dir, "originality_report.json")
        payload = {
            "content_analysis": analysis,
            "youtube": youtube,
            "facebook": facebook,
            "hook_variants": hooks_data.get("hooks", []),
            "content_edit_plan": edit_plan,
            "originality_report": originality,
        }
        self._write_supporting_files(pack_dir=pack_dir, payload=payload, profile=profile)
        all_errors = [
            item.get("error") for item in state.get("components", {}).values()
            if item.get("status") == "failed" and item.get("error")
        ]
        warnings = list(dict.fromkeys([*warnings, *[str(item) for item in all_errors]]))
        publish_ready_path = pack_dir / "publish_ready.mp4"
        manifest_path = self._write_manifest(
            pack_dir=pack_dir, config=config, final_video_path=final_video_path,
            source_url=source_url, job_id=job_id,
            recommended_title=str(youtube.get("recommended_title") or facebook.get("recommended_title") or ""),
            warnings=warnings, state=state,
        )
        overall = _overall_component_status(state)
        return PublishingPackResult(
            pack_dir=pack_dir,
            manifest_path=manifest_path,
            youtube_metadata_path=(pack_dir / "youtube_metadata.json") if (pack_dir / "youtube_metadata.json").is_file() else None,
            facebook_metadata_path=(pack_dir / "facebook_metadata.json") if (pack_dir / "facebook_metadata.json").is_file() else None,
            publish_ready_video_path=publish_ready_path if publish_ready_path.is_file() else None,
            recommended_title=str(youtube.get("recommended_title") or facebook.get("recommended_title") or ""),
            warnings=warnings,
            overall_status=overall,
            component_status_path=_component_status_path(pack_dir),
        )

def _clean_source_metadata(raw: Dict[str, Any], source_url: str, source_path: Optional[Path]) -> Dict[str, Any]:
    raw = dict(raw or {})
    return {
        "source_url": source_url,
        "final_url": str(raw.get("final_url") or source_url),
        "platform": str(raw.get("platform") or ""),
        "title": str(raw.get("title") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "uploader": str(raw.get("uploader") or "").strip(),
        "thumbnail_url": str(raw.get("thumbnail_url") or "").strip(),
        "duration": float(raw.get("duration") or 0.0),
        "tags": [str(item) for item in (raw.get("tags") or []) if str(item).strip()][:50],
        "source_file": Path(source_path).name if source_path else None,
    }


def _clean_segments(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        try:
            start = max(0.0, float(item.get("start", 0.0)))
            end = max(start, float(item.get("end", start)))
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        cleaned.append({"start": start, "end": end, "text": text})
    return cleaned


def _all_profile_keywords(profile: Dict[str, Any]) -> List[str]:
    result: list[str] = []
    for group in (profile.get("primary_keyword_groups") or {}).values():
        for item in group:
            value = str(item).strip()
            if value and value not in result:
                result.append(value)
    return result


def _deterministic_pack(
    *,
    profile: Dict[str, Any],
    config: PublishingPackConfig,
    source_metadata: Dict[str, Any],
    translated_segments: Sequence[Dict[str, Any]],
    translated_text: str,
    source_text: str,
) -> Dict[str, Any]:
    sentences = _sentences(translated_text)
    source_title = source_metadata.get("title") or ""
    story_name, story_confidence = _story_name(source_title)
    keywords = _all_profile_keywords(profile)
    lower = translated_text.lower()
    scored = sorted(((lower.count(keyword.lower()), keyword) for keyword in keywords), reverse=True)
    niche_match_score, niche_matches = _niche_match(profile, source_title, translated_text)
    off_niche = profile.get("id") == "van_diep_studio" and niche_match_score < 0.25
    primary_keyword = next((keyword for score, keyword in scored if score > 0), None)
    secondary_keywords = [keyword for score, keyword in scored if score > 0 and keyword != primary_keyword][:3]
    if not primary_keyword and not off_niche:
        primary_keyword = "review truyện tu tiên"
    if not secondary_keywords and not off_niche:
        secondary_keywords = [keyword for keyword in keywords if keyword != primary_keyword][:3]
    strongest_hook = _strongest_sentence(sentences) or source_title or "Nội dung video có một chi tiết đáng chú ý ngay từ đầu"
    conflict = sentences[0] if sentences else strongest_hook
    payoff = sentences[-1] if sentences else "Video khép lại bằng một thông tin đáng chú ý"
    title_core = _compact_title_clause(strongest_hook, 68 if off_niche else 54)
    recommended = (
        _limit_title(title_core)
        if off_niche else
        _limit_title(f"{title_core} | {str(primary_keyword).title()}")
    )
    if off_niche:
        alt_titles = [
            _limit_title(_compact_title_clause(conflict, 90)),
            _limit_title(_compact_title_clause(payoff, 90)),
            _limit_title(f"{title_core} – {profile['channel_name']}"),
        ]
    else:
        alt_titles = [
            _limit_title(f"{_compact_title_clause(conflict, 60)} | Review Truyện Tu Tiên"),
            _limit_title(f"{_compact_title_clause(payoff, 60)} | {str(primary_keyword).title()}"),
            _limit_title(f"{title_core} – {profile['channel_name']}"),
        ]
    episode_summary = _summary_from_sentences(sentences)
    playlist_url = config.playlist_url or "[DÁN LINK PLAYLIST]"
    if off_niche:
        topic_terms = _topic_terms(f"{source_title} {translated_text}")
        hashtags = _dedupe([f"#{_hashtag_term(profile['channel_name'])}", *[f"#{_hashtag_term(item)}" for item in topic_terms[:4]]])
        description = (
            f"{episode_summary}\n\n"
            f"Video được thuyết minh và biên tập lại bằng tiếng Việt bởi {profile['channel_name']}.\n\n"
            f"{' '.join(hashtags)}"
        )
        specific_tags = _dedupe([source_title, *topic_terms])
        tags = [str(item).strip() for item in specific_tags if str(item).strip()][:15]
    else:
        hashtags = _dedupe([*profile.get("base_hashtags", []), "#TuTiên", "#TiênHiệp"])
        description = profile["description_template"].format(
            story_name=story_name,
            episode_journey=_lower_first(_compact_title_clause(conflict, 160)),
            episode_summary=episode_summary,
            playlist_url=playlist_url,
            hashtags=" ".join(hashtags),
        )
        specific_tags = _specific_tags(story_name, translated_text)
        tags = _dedupe([*profile.get("base_tags", []), primary_keyword, *secondary_keywords, *specific_tags])[:15]
    thumbnail_texts = _thumbnail_phrases([strongest_hook, conflict, payoff], profile)
    hooks = _dedupe([
        _ensure_hook(strongest_hook),
        _ensure_hook(conflict),
        f"Không ai ngờ rằng {_lower_first(_compact_title_clause(payoff, 90))}",
    ])[:3]
    facebook_title = _limit_title(_compact_title_clause(strongest_hook, 80), 100)
    facebook_caption = f"{facebook_title}\n\n{episode_summary}\n\nBạn nghĩ bước ngoặt này sẽ đưa gia tộc đi đến đâu?\n\n{' '.join(hashtags[:5])}"
    return {
        "content_analysis": {
            "content_type": "off_niche" if off_niche else "review_truyen_tu_tien",
            "niche_match_score": niche_match_score,
            "niche_matches": niche_matches,
            "channel_fit": "review_before_publish" if off_niche else "matched",
            "story_name": story_name,
            "story_name_confidence": story_confidence,
            "main_character": "Chưa xác định",
            "main_conflict": conflict,
            "payoff": payoff,
            "primary_keyword": primary_keyword,
            "secondary_keywords": secondary_keywords,
            "strongest_hook": strongest_hook,
            "summary": episode_summary,
            "source_language_excerpt": source_text[:1000],
        },
        "youtube": {
            "recommended_title": recommended,
            "alternative_titles": _dedupe(alt_titles)[:5],
            "description": description,
            "hashtags": hashtags[:5],
            "tags": tags,
            "category": profile.get("category", "Entertainment"),
            "language": "vi",
            "default_language": "vi",
            "privacy_status": profile.get("default_privacy", "private"),
            "made_for_kids": bool(profile.get("made_for_kids", False)),
            "playlist_url": config.playlist_url,
        },
        "facebook": {
            "recommended_title": facebook_title,
            "alternative_titles": _dedupe(alt_titles)[:3],
            "caption": facebook_caption,
            "hashtags": hashtags[:5],
        },
        "thumbnail_texts": thumbnail_texts,
        "hook_variants": hooks,
        "content_edit_plan": {
            "edit_level": config.edit_level,
            "opening_hook": hooks[0] if hooks else strongest_hook,
            "opening_overlay_seconds": 3.5,
            "remove_or_review": [
                "Intro hoặc lời kêu gọi theo dõi của nguồn gốc",
                "Câu lặp, khoảng lặng và đoạn không còn thông tin mới",
                "Logo hoặc chữ nguồn còn sót lại",
            ],
            "value_additions": [
                "Thêm câu dẫn tiếng Việt mới ở 3–5 giây đầu",
                "Thêm chú thích tên nhân vật, gia tộc, hệ thống hoặc cảnh giới khi xác định chắc chắn",
                "Kết thúc bằng câu hỏi kéo bình luận thay vì sao chép CTA nguồn",
            ],
            "publish_ready_variant": bool(config.generate_publish_ready_video),
        },
        "originality_report": {
            "status": "needs_human_review",
            "score": 62 if config.edit_level == "balanced" else 48 if config.edit_level == "light" else 72,
            "added_value": [
                "Bản dịch và voice tiếng Việt",
                "Subtitle mới và branding",
                "Title, description và thumbnail mới theo nội dung thực tế",
                "Hook/CTA overlay mới trong bản publish-ready" if config.generate_publish_ready_video else "Kế hoạch hook/CTA mới",
            ],
            "warnings": [
                *(["Nội dung có vẻ không khớp ngách tu tiên/gia tộc của Vạn Diệp Studio; nên duyệt lại trước khi đăng."] if off_niche else []),
                "Không thể bảo đảm nền tảng coi video là nguyên bản chỉ dựa trên tự động hóa.",
                "Cần xem lại quyền sử dụng nguồn, tính chính xác và mức độ đóng góp sáng tạo trước khi đăng.",
            ],
        },
    }


def _publishing_prompt(
    *,
    profile: Dict[str, Any],
    config: PublishingPackConfig,
    source_metadata: Dict[str, Any],
    translated_text: str,
    source_text: str,
) -> str:
    keyword_groups = json.dumps(profile.get("primary_keyword_groups", {}), ensure_ascii=False)
    return f"""
Bạn là biên tập viên YouTube/Facebook cho kênh {profile['channel_name']}.
Hãy phân tích nội dung video THỰC TẾ từ transcript tiếng Việt, không bịa tên truyện,
nhân vật hoặc dữ kiện không có căn cứ. Nếu chưa chắc tên truyện, ghi "Chưa xác định"
và confidence dưới 0.5. Không được ép keyword tu tiên vào video ngoài ngách. Nếu nội
dung không khớp ngách kênh, phải đánh dấu channel_fit="review_before_publish", mô tả
đúng nội dung thực tế và cảnh báo người dùng thay vì bịa thành truyện tu tiên.

NGÁCH KÊNH: {profile['niche']}
ĐỐI TƯỢNG: {profile['audience']}
NHÓM KEYWORD ĐƯỢC PHÉP: {keyword_groups}
CÔNG THỨC TITLE: {profile['title_formula']}
PHONG CÁCH: {config.style}
MỨC BIÊN TẬP: {config.edit_level}
HƯỚNG DẪN RIÊNG: {config.custom_instructions or 'Không có'}

METADATA NGUỒN:
{json.dumps(source_metadata, ensure_ascii=False)}

TRANSCRIPT TIẾNG VIỆT:
{translated_text[:24000]}

TRÍCH ĐOẠN NGÔN NGỮ NGUỒN:
{source_text[:5000]}

Trả về đúng một JSON object với cấu trúc:
{{
  "content_analysis": {{
    "niche_match_score": 0.0,
    "channel_fit": "matched hoặc review_before_publish",
    "story_name": "...",
    "story_name_confidence": 0.0,
    "main_character": "...",
    "main_conflict": "...",
    "payoff": "...",
    "primary_keyword": "chỉ 1 keyword sát nội dung",
    "secondary_keywords": ["tối đa 3"],
    "strongest_hook": "...",
    "summary": "2-4 câu"
  }},
  "youtube": {{
    "recommended_title": "<=100 ký tự",
    "alternative_titles": ["5 phương án gồm search/curiosity/an toàn"],
    "description": "mô tả riêng cho tập, 2 dòng đầu nói đúng nội dung",
    "hashtags": ["3-5 hashtag"],
    "tags": ["8-15 tags, có tên truyện/nhân vật nếu chắc chắn"]
  }},
  "facebook": {{
    "recommended_title": "...",
    "alternative_titles": ["3 phương án"],
    "caption": "caption ngắn, có câu hỏi cuối",
    "hashtags": ["3-5 hashtag"]
  }},
  "thumbnail_texts": ["3 câu, mỗi câu 2-5 từ, không lặp nguyên title"],
  "hook_variants": ["3 hook mở đầu 1-2 câu"],
  "content_edit_plan": {{
    "opening_hook": "...",
    "remove_or_review": ["..."],
    "value_additions": ["..."]
  }},
  "originality_report": {{
    "score": 0,
    "added_value": ["..."],
    "warnings": ["..."]
  }}
}}
""".strip()


def _merge_and_normalize_pack(
    fallback: Dict[str, Any],
    ai: Optional[Dict[str, Any]],
    profile: Dict[str, Any],
    config: PublishingPackConfig,
) -> Dict[str, Any]:
    if not ai:
        return fallback
    result = json.loads(json.dumps(fallback, ensure_ascii=False))
    for section in ("content_analysis", "youtube", "facebook", "content_edit_plan", "originality_report"):
        if isinstance(ai.get(section), dict):
            result[section].update({k: v for k, v in ai[section].items() if v not in (None, "", [])})
    for key in ("thumbnail_texts", "hook_variants"):
        if isinstance(ai.get(key), list) and ai[key]:
            result[key] = ai[key]

    analysis = result["content_analysis"]
    story = str(analysis.get("story_name") or "Chưa xác định").strip()
    confidence = _clamp_float(analysis.get("story_name_confidence"), 0.0, 1.0, 0.0)
    if confidence < 0.5 and story not in {"Chưa xác định", "Không xác định"}:
        story = "Chưa xác định"
    analysis["story_name"] = story
    analysis["story_name_confidence"] = confidence
    niche_match_score = _clamp_float(
        analysis.get("niche_match_score"), 0.0, 1.0,
        fallback["content_analysis"].get("niche_match_score", 0.0),
    )
    analysis["niche_match_score"] = niche_match_score
    off_niche = profile.get("id") == "van_diep_studio" and niche_match_score < 0.25
    analysis["channel_fit"] = "review_before_publish" if off_niche else "matched"
    allowed_keywords = set(_all_profile_keywords(profile))
    primary = str(analysis.get("primary_keyword") or fallback["content_analysis"].get("primary_keyword") or "").strip().lower()
    if off_niche:
        primary = ""
    elif primary not in allowed_keywords:
        primary = str(fallback["content_analysis"].get("primary_keyword") or "review truyện tu tiên")
    analysis["primary_keyword"] = primary
    secondary = [str(item).strip().lower() for item in analysis.get("secondary_keywords", []) if str(item).strip().lower() in allowed_keywords and str(item).strip().lower() != primary]
    analysis["secondary_keywords"] = [] if off_niche else _dedupe(secondary)[:3]

    youtube = result["youtube"]
    youtube["recommended_title"] = _limit_title(str(youtube.get("recommended_title") or fallback["youtube"]["recommended_title"]))
    youtube["alternative_titles"] = [_limit_title(str(item)) for item in youtube.get("alternative_titles", []) if str(item).strip()][:5]
    youtube["hashtags"] = _normalize_hashtags(youtube.get("hashtags", []), profile.get("base_hashtags", []))[:5]
    youtube["tags"] = _dedupe([str(item).strip() for item in youtube.get("tags", []) if str(item).strip()])[:15]
    youtube.update({
        "category": profile.get("category", "Entertainment"),
        "language": "vi",
        "default_language": "vi",
        "privacy_status": profile.get("default_privacy", "private"),
        "made_for_kids": bool(profile.get("made_for_kids", False)),
        "playlist_url": config.playlist_url,
    })
    facebook = result["facebook"]
    facebook["recommended_title"] = _limit_title(str(facebook.get("recommended_title") or youtube["recommended_title"]), 100)
    facebook["hashtags"] = _normalize_hashtags(facebook.get("hashtags", []), profile.get("base_hashtags", []))[:5]
    result["thumbnail_texts"] = [_thumbnail_text(str(item), profile) for item in result.get("thumbnail_texts", [])][:3]
    while len(result["thumbnail_texts"]) < 3:
        result["thumbnail_texts"].append(fallback["thumbnail_texts"][len(result["thumbnail_texts"])])
    result["hook_variants"] = _dedupe([_ensure_hook(str(item)) for item in result.get("hook_variants", []) if str(item).strip()])[:3] or fallback["hook_variants"]
    return result


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…。！？])\s+|\n+", text or "")
    return [" ".join(part.split()) for part in parts if len(" ".join(part.split())) >= 8]


def _strongest_sentence(sentences: Sequence[str]) -> str:
    if not sentences:
        return ""
    cues = ("chỉ còn", "bỗng", "vạn năm", "hệ thống", "lão tổ", "diệt vong", "quật khởi", "không ai", "cuối cùng", "đột nhiên", "xuyên không", "linh mạch")
    def score(sentence: str) -> float:
        lower = sentence.lower()
        return sum(3 for cue in cues if cue in lower) + min(4, len(re.findall(r"\d+", sentence))) + min(3, len(sentence) / 50)
    return max(sentences[:80], key=score)


def _story_name(source_title: str) -> tuple[str, float]:
    title = " ".join(str(source_title or "").split())
    if not title or re.fullmatch(r"douyin_?\d*", title.lower()):
        return "Chưa xác định", 0.0

    explicit_patterns = [
        r"《([^》]{2,100})》",
        r"【([^】]{2,100})】",
        r"(?:tên truyện|truyện)\s*[:：]\s*([^#|]{2,100})",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, title, flags=re.I)
        if match:
            return " ".join(match.group(1).split())[:120], 0.92

    cleaned = re.sub(r"[#@].*$", "", title).strip(" -|_.,")
    # Douyin titles are frequently full descriptions rather than work names.
    # Only accept a concise title-like phrase; otherwise require AI/user review.
    words = cleaned.split()
    story_cues = ("tu tiên", "tiên hiệp", "gia tộc", "lão tổ", "trường sinh", "tông môn", "xuyên không", "hệ thống")
    looks_like_story = (
        2 <= len(words) <= 14
        and len(cleaned) <= 90
        and any(cue in cleaned.lower() for cue in story_cues)
        and not re.search(r"[.!?。！？]", cleaned)
    )
    if looks_like_story:
        return cleaned[:120], 0.66
    return "Chưa xác định", 0.15


def _niche_match(profile: Dict[str, Any], source_title: str, translated_text: str) -> tuple[float, List[str]]:
    if profile.get("id") != "van_diep_studio":
        return 1.0, ["generic_profile"]
    text = f"{source_title} {translated_text}".lower()
    cues = [
        "tu tiên", "tiên hiệp", "gia tộc", "lão tổ", "trường sinh",
        "tông môn", "linh mạch", "linh căn", "tu luyện", "cảnh giới",
        "đan dược", "tiên tộc", "xuyên không", "bế quan", "đệ tử",
    ]
    matches = [cue for cue in cues if cue in text]
    phrase_hits = sum(1 for keyword in _all_profile_keywords(profile) if keyword in text)
    score = min(1.0, len(matches) * 0.13 + phrase_hits * 0.22)
    return round(score, 3), matches[:12]


def _topic_terms(text: str) -> List[str]:
    stopwords = {
        "trong", "những", "được", "của", "một", "và", "là", "có", "không",
        "này", "đó", "với", "cho", "khi", "đến", "từ", "video", "thì", "như",
        "theo", "đang", "sẽ", "đã", "các", "người", "rất", "về", "tại",
    }
    words = re.findall(r"[A-Za-zÀ-ỹĐđ]{3,}", text.lower())
    counts: Dict[str, int] = {}
    for word in words:
        if word in stopwords:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]]


def _hashtag_term(text: str) -> str:
    value = _remove_accents(str(text or ""))
    return re.sub(r"[^A-Za-z0-9]", "", value) or "VideoTiengViet"


def _is_public_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not _is_public_http_url(newurl):
            raise ValueError("Thumbnail redirect points to a private or invalid address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_source_thumbnail(url: str, pack_dir: Path, *, logger=None) -> Optional[Path]:
    url = str(url or "").strip()
    if not _is_public_http_url(url):
        return None
    logger = logger or _logger
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 UVAI-PublishingPack/1.0"},
    )
    try:
        opener = urllib.request.build_opener(_PublicRedirectHandler())
        with opener.open(request, timeout=20) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024 or len(data) < 128:
            return None
        is_jpeg = data.startswith(b"\xff\xd8\xff")
        is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
        is_webp = len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        if not (is_jpeg or is_png or is_webp):
            return None
        extension = ".png" if is_png or "png" in content_type else ".webp" if is_webp or "webp" in content_type else ".jpg"
        output = Path(pack_dir) / f"source_thumbnail{extension}"
        output.write_bytes(data)
        return output
    except Exception as exc:
        logger.info("Could not download source thumbnail %s: %s", url, exc)
        return None


def _summary_from_sentences(sentences: Sequence[str]) -> str:
    if not sentences:
        return "Video kể lại một bước ngoặt quan trọng trong hành trình tu tiên và phát triển thế lực."
    selected = list(sentences[:2])
    if len(sentences) > 3:
        selected.append(sentences[-1])
    return " ".join(_compact_title_clause(item, 180) for item in selected)[:650]


def _specific_tags(story_name: str, text: str) -> List[str]:
    tags: list[str] = []
    if story_name and story_name != "Chưa xác định":
        tags.extend([story_name, _remove_accents(story_name)])
    for match in re.findall(r"(?:gia tộc|tông môn|lão tổ|hệ thống)\s+[A-ZÀ-Ỹa-zà-ỹ][\wÀ-ỹ-]{1,20}", text, flags=re.I):
        tags.append(match)
    return _dedupe(tags)[:6]


def _remove_accents(text: str) -> str:
    import unicodedata
    normalized = unicodedata.normalize("NFD", text)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn").replace("đ", "d").replace("Đ", "D")


def _thumbnail_phrases(candidates: Sequence[str], profile: Dict[str, Any]) -> List[str]:
    phrases = [_thumbnail_text(item, profile) for item in candidates if item]
    phrases = _dedupe(phrases)
    for example in profile.get("thumbnail_rules", {}).get("examples", []):
        if len(phrases) >= 3:
            break
        phrases.append(example)
    return phrases[:3]


def _thumbnail_text(text: str, profile: Dict[str, Any]) -> str:
    max_words = int(profile.get("thumbnail_rules", {}).get("max_words", 5))
    clean = re.sub(r"[^\wÀ-ỹ\s]", " ", text.upper())
    words = [word for word in clean.split() if len(word) > 1]
    cue_words = ["LÃO", "TỔ", "GIA", "TỘC", "TRƯỜNG", "SINH", "HỆ", "THỐNG", "QUẬT", "KHỞI", "VẠN", "NĂM", "TIÊN", "TỘC", "TRỞ", "VỀ"]
    selected: list[str] = []
    for word in words:
        if word in cue_words and word not in selected:
            selected.append(word)
        if len(selected) >= max_words:
            break
    if len(selected) < 2:
        selected = words[:max_words]
    return " ".join(selected[:max_words]) or "BƯỚC NGOẶT TU TIÊN"


def _ensure_hook(text: str) -> str:
    text = _compact_title_clause(text, 180).strip()
    if not text:
        return "Một biến cố không ai ngờ tới đã thay đổi toàn bộ vận mệnh của gia tộc."
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def _compact_title_clause(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split()).strip(" .,-|_")
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0]
    return (cut or clean[:limit]).rstrip(" ,.-") + "…"


def _limit_title(text: str, limit: int = 100) -> str:
    return _compact_title_clause(text, limit)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _normalize_hashtags(items: Iterable[Any], defaults: Iterable[str]) -> List[str]:
    result: list[str] = []
    for item in [*items, *defaults]:
        value = re.sub(r"\s+", "", str(item or "").strip())
        if not value:
            continue
        if not value.startswith("#"):
            value = "#" + value
        if value not in result:
            result.append(value)
    return result


def _dedupe(items: Iterable[Any]) -> List[Any]:
    result = []
    seen = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _probe_video_geometry(video_path: Path) -> tuple[int, int]:
    if shutil.which("ffprobe") is None:
        return 0, 0
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = re.search(r"(\d+)x(\d+)", result.stdout or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _probe_duration(video_path: Path) -> float:
    if shutil.which("ffprobe") is None:
        return 0.0
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        return max(0.0, float((result.stdout or "0").strip()))
    except ValueError:
        return 0.0


def _thumbnail_timestamps(segments: Sequence[Dict[str, Any]], hook: str, duration: float, count: int) -> List[float]:
    count = max(1, min(3, count))
    candidates: list[float] = []
    hook_words = set(re.findall(r"\w+", hook.lower()))
    scored = []
    for item in segments:
        words = set(re.findall(r"\w+", item["text"].lower()))
        overlap = len(hook_words & words)
        scored.append((overlap, item["start"], item["end"]))
    for overlap, start, end in sorted(scored, reverse=True):
        if overlap <= 0:
            break
        candidates.append((start + end) / 2)
        if len(candidates) >= count:
            break
    fallback_ratios = (0.16, 0.48, 0.78)
    for ratio in fallback_ratios:
        if len(candidates) >= count:
            break
        candidates.append(duration * ratio if duration > 0 else 1.0 + len(candidates) * 2.0)
    max_time = max(0.1, duration - 0.2) if duration > 0 else 99999
    return [max(0.0, min(max_time, value)) for value in candidates[:count]]


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "’")


def _render_thumbnail(video_path: Path, output_path: Path, *, timestamp: float, text: str, width: int, height: int) -> None:
    font_path = _resolve_font_path(None)
    text_file = output_path.with_suffix(".txt")
    wrapped = _wrap_thumbnail_text(text)
    text_file.write_text(wrapped, encoding="utf-8")
    font = f"fontfile='{_escape_filter_path(font_path)}':" if font_path else "font='Sans':"
    font_size = max(34, round(width * 0.055))
    box_y = round(height * 0.60)
    box_h = height - box_y
    text_y = round(height * 0.70)
    filter_graph = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        "eq=contrast=1.08:saturation=1.08,"
        f"drawbox=x=0:y={box_y}:w={width}:h={box_h}:color=black@0.50:t=fill,"
        f"drawtext={font}textfile='{_escape_filter_path(str(text_file))}':"
        f"fontsize={font_size}:fontcolor=white:borderw=3:bordercolor=black@0.85:"
        f"line_spacing=8:x=(w-text_w)/2:y={text_y}-text_h/2"
    )
    cmd = [
        "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path),
        "-frames:v", "1", "-vf", filter_graph, "-q:v", "2", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    text_file.unlink(missing_ok=True)
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError((result.stderr or "FFmpeg thumbnail failed")[-800:])


def _wrap_thumbnail_text(text: str) -> str:
    words = str(text or "").upper().split()
    if len(words) <= 3:
        return " ".join(words)
    midpoint = math.ceil(len(words) / 2)
    return " ".join(words[:midpoint]) + "\n" + " ".join(words[midpoint:])


def _render_publish_ready_video(
    source: Path,
    output: Path,
    *,
    hook: str,
    cta: str,
    duration: float,
    edit_level: str,
) -> None:
    font_path = _resolve_font_path(None)
    hook_file = output.with_name(".publish_hook.txt")
    cta_file = output.with_name(".publish_cta.txt")
    hook_file.write_text(textwrap.fill(_compact_title_clause(hook, 150), width=36), encoding="utf-8")
    cta_file.write_text(textwrap.fill(_compact_title_clause(cta, 100), width=34), encoding="utf-8")
    font = f"fontfile='{_escape_filter_path(font_path)}':" if font_path else "font='Sans':"
    video_w, video_h = _probe_video_geometry(source)
    if video_w <= 0 or video_h <= 0:
        raise RuntimeError("Không đọc được kích thước video để tạo publish-ready")
    base = min(video_w, video_h)
    hook_font_size = max(24, min(72, round(base * 0.045)))
    cta_font_size = max(20, min(58, round(base * 0.032)))
    hook_duration = 3.5 if edit_level != "deep" else 4.5
    cta_start = max(hook_duration + 1.0, duration - 3.0) if duration > 0 else 99999
    hook_x = round(video_w * 0.05)
    hook_y = round(video_h * 0.06)
    hook_w = round(video_w * 0.90)
    hook_h = round(video_h * 0.20)
    hook_text_y = round(video_h * 0.11)
    vf = (
        f"drawbox=x={hook_x}:y={hook_y}:w={hook_w}:h={hook_h}:color=black@0.48:t=fill:enable='between(t,0,{hook_duration:.2f})',"
        f"drawtext={font}textfile='{_escape_filter_path(str(hook_file))}':fontsize={hook_font_size}:"
        f"fontcolor=white:borderw=2:bordercolor=black@0.9:line_spacing=6:x=(w-text_w)/2:y={hook_text_y}:enable='between(t,0,{hook_duration:.2f})'"
    )
    if duration > 4:
        cta_x = round(video_w * 0.08)
        cta_y = round(video_h * 0.77)
        cta_w = round(video_w * 0.84)
        cta_h = round(video_h * 0.12)
        cta_text_y = round(video_h * 0.80)
        vf += (
            f",drawbox=x={cta_x}:y={cta_y}:w={cta_w}:h={cta_h}:color=black@0.45:t=fill:enable='gte(t,{cta_start:.2f})',"
            f"drawtext={font}textfile='{_escape_filter_path(str(cta_file))}':fontsize={cta_font_size}:"
            f"fontcolor=white:borderw=2:bordercolor=black@0.8:x=(w-text_w)/2:y={cta_text_y}:enable='gte(t,{cta_start:.2f})'"
        )
    cmd = [
        "ffmpeg", "-y", "-i", str(source), "-vf", vf,
        "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(180, int(duration * 6) if duration else 180))
    hook_file.unlink(missing_ok=True)
    cta_file.unlink(missing_ok=True)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError((result.stderr or "FFmpeg publish-ready render failed")[-1000:])


def _markdown_pack(payload: Dict[str, Any], profile: Dict[str, Any]) -> str:
    yt = payload["youtube"]
    fb = payload["facebook"]
    analysis = payload["content_analysis"]
    return f"""# AI Publishing Pack — {profile['channel_name']}

## Phân tích nội dung
- Tên truyện: {analysis.get('story_name', 'Chưa xác định')}
- Độ tin cậy tên truyện: {analysis.get('story_name_confidence', 0)}
- Keyword chính: {analysis.get('primary_keyword', '')}
- Mâu thuẫn: {analysis.get('main_conflict', '')}
- Hook mạnh nhất: {analysis.get('strongest_hook', '')}

## YouTube
### Tiêu đề đề xuất
{yt.get('recommended_title', '')}

### Tiêu đề thay thế
""" + "\n".join(f"- {item}" for item in yt.get("alternative_titles", [])) + f"""

### Mô tả
{yt.get('description', '')}

### Hashtag
{' '.join(yt.get('hashtags', []))}

### Tags
{', '.join(yt.get('tags', []))}

## Facebook
### Tiêu đề
{fb.get('recommended_title', '')}

### Caption
{fb.get('caption', '')}

### Hashtag
{' '.join(fb.get('hashtags', []))}

## Hook mở đầu
""" + "\n".join(f"- {item}" for item in payload.get("hook_variants", [])) + "\n"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(str(text or ""), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
