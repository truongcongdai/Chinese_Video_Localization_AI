"""CP7B execution of approved CP7A assets through the existing media stack."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from universal_video_ai.content_os.renderer import MP4Validator, ValidationStatus
from universal_video_ai.channel_agent.production_assets import ProductionAssetService
from universal_video_ai.localization_coverage import validate_timeline_coverage
from universal_video_ai.mixer.service import AudioMix, MixerService
from universal_video_ai.orchestrator.service import LocalizationConfig, LocalizationService
from universal_video_ai.render.renderer import RenderConfig, Renderer, TextOverlay
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.timeline.service import TimelineService
from universal_video_ai.tts.registry import RegistryTTSBackend
from universal_video_ai.tts.service import TTSService
from universal_video_ai.web.store import Store


class ProductionRenderError(RuntimeError):
    pass


class ProductionRenderNotFound(ProductionRenderError):
    pass


class ProductionRenderService:
    STAGES = {
        "queued", "preparing", "tts", "subtitle", "visual_cleanup",
        "mixing", "rendering", "qc", "completed", "failed",
    }
    RIGHTS_ATTESTATIONS = {"owned", "licensed", "public_domain"}
    LANGUAGE_CODES = {
        "vietnamese": "vi",
        "tiếng việt": "vi",
        "viet": "vi",
        "vi-vn": "vi",
    }

    def __init__(
        self,
        store: Store,
        *,
        tts_service: Optional[TTSService] = None,
        mixer: Optional[MixerService] = None,
        renderer: Optional[Renderer] = None,
        timeline: Optional[TimelineService] = None,
        output_root: Optional[Path] = None,
    ) -> None:
        self.store = store
        self.tts_service = tts_service or TTSService(backend=RegistryTTSBackend())
        self.mixer = mixer or MixerService()
        self.renderer = renderer or Renderer(RenderConfig())
        self.timeline = timeline or TimelineService()
        db_path = Path(getattr(store, "db_path", "local_data/app.db")).resolve()
        self.output_root = (output_root or db_path.parent / "production_renders").resolve()

    def _item(self, user_id: int, item_id: int) -> dict:
        item = self.store.get_production_item(user_id, item_id)
        if not item:
            raise ProductionRenderNotFound("Production item not found.")
        return item

    def _approved(self, user_id: int, item_id: int) -> Dict[str, dict]:
        result = {}
        for asset_type in (
            "script_draft", "visual_plan", "voice_plan",
            "thumbnail_brief", "metadata_package",
        ):
            rows = self.store.list_production_assets(
                user_id, item_id, asset_type=asset_type, status="approved"
            )
            if rows:
                result[asset_type] = rows[0]
        return result

    def _validate_assets(self, user_id: int, item_id: int) -> Dict[str, dict]:
        item = self._item(user_id, item_id)
        approved = self._approved(user_id, item_id)
        required = {"script_draft", "visual_plan", "voice_plan", "metadata_package"}
        if item.get("target_format") != "short_form":
            required.add("thumbnail_brief")
        missing = sorted(required - set(approved))
        if missing:
            raise ProductionRenderError(
                "Approved CP7A assets required before render: " + ", ".join(missing)
            )
        # CP7A package/QA evaluation is deterministic and does not use its AI
        # provider. Reuse it here so manually inserted or malformed approved
        # rows cannot bypass the approved Production Asset Package gate.
        package = ProductionAssetService(  # type: ignore[arg-type]
            self.store, provider=None
        ).package(user_id, item_id)
        if not package["planning_ready"] or not package["asset_ready"]:
            detail = "; ".join(package.get("reasons") or []) or "planning is not ready"
            raise ProductionRenderError(
                "Approved CP7A Production Asset Package is not ready: " + detail
            )
        return approved

    def submit(
        self,
        user_id: int,
        item_id: int,
        *,
        source_video_path: str,
        source_media_rights: str,
        voice_id: Optional[str] = None,
        speaker_mapping: Optional[Dict[str, str]] = None,
        preserve_source_audio: bool = False,
        source_subtitle_boxes: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        approved = self._validate_assets(user_id, item_id)
        source = Path(source_video_path).resolve()
        if not source.is_file():
            raise ProductionRenderError("Source video does not exist.")
        if source_media_rights not in self.RIGHTS_ATTESTATIONS:
            raise ProductionRenderError(
                "Source media must be attested as owned, licensed, or public_domain."
            )
        request = {
            "source_video_path": str(source),
            "source_media_rights": source_media_rights,
            "voice_id": voice_id,
            "speaker_mapping": speaker_mapping or {},
            "preserve_source_audio": bool(preserve_source_audio),
            "source_subtitle_boxes": source_subtitle_boxes or [],
        }
        refs = {
            kind: {"asset_id": row["id"], "version": row["version"]}
            for kind, row in approved.items()
        }
        job = self.store.create_production_render_job(
            user_id, item_id, request=request, approved_asset_refs=refs
        )
        if not job:
            raise ProductionRenderNotFound("Production item not found.")
        self.store.add_production_event(
            user_id, item_id, event_type="production_render_queued",
            note=f"CP7B render job {job['id']} queued",
        )
        return job

    def get(self, user_id: int, item_id: int, job_id: int) -> dict:
        self._item(user_id, item_id)
        job = self.store.get_production_render_job(user_id, item_id, job_id)
        if not job:
            raise ProductionRenderNotFound("Production render job not found.")
        return job

    def list(self, user_id: int, item_id: int) -> List[dict]:
        self._item(user_id, item_id)
        return self.store.list_production_render_jobs(user_id, item_id)

    def _stage(self, user_id: int, item_id: int, job_id: int, stage: str, progress: float) -> None:
        if stage not in self.STAGES:
            raise ProductionRenderError(f"Invalid render stage: {stage}")
        status = "completed" if stage == "completed" else ("failed" if stage == "failed" else "running")
        self.store.update_production_render_job(
            user_id, item_id, job_id,
            {"status": status, "current_stage": stage, "progress": progress},
        )

    def _section_segments(
        self, user_id: int, item_id: int, script: dict,
        timing_boxes: Optional[List[Dict[str, Any]]] = None,
    ) -> List[TranscriptSegment]:
        rows = []
        cursor = 0.0
        sources = script["payload"].get("source_section_versions") or []
        use_observed_timing = bool(timing_boxes) and len(timing_boxes) == len(sources)
        for source_index, source in enumerate(sources):
            asset = self.store.get_production_asset(
                user_id, item_id, int(source.get("asset_id") or 0)
            )
            if not asset or asset["asset_type"] != "script_section":
                raise ProductionRenderError("Approved script references a missing section.")
            payload = asset["payload"]
            duration = max(0.2, float(payload.get("target_duration_minutes") or 0) * 60)
            text = str(payload.get("content") or "").strip()
            if not text:
                raise ProductionRenderError("Approved script section is empty.")
            if use_observed_timing:
                box = timing_boxes[source_index]
                start = float(box.get("start"))
                end = float(box.get("end"))
                if start < 0 or end <= start:
                    raise ProductionRenderError("Observed subtitle timing is invalid.")
            else:
                start, end = cursor, cursor + duration
            rows.append(TranscriptSegment(start, end, text))
            cursor = end
        if not rows:
            raise ProductionRenderError("Approved script has no source sections.")
        return rows

    @staticmethod
    def _voice_for_section(
        index: int, voice_plan: dict, request: dict
    ) -> Optional[str]:
        mapping = request.get("speaker_mapping") or {}
        notes = voice_plan.get("section_delivery_notes") or []
        role = "narrator"
        if isinstance(notes, list) and index < len(notes) and isinstance(notes[index], dict):
            role = str(notes[index].get("speaker_role") or notes[index].get("role") or role)
        return mapping.get(role) or request.get("voice_id") or voice_plan.get("voice_id")

    @classmethod
    def _voice_language(cls, voice_plan: dict) -> str:
        value = str(voice_plan.get("language") or "vi").strip().casefold()
        return cls.LANGUAGE_CODES.get(value, value.split("-", 1)[0] or "vi")

    @staticmethod
    def _probe(path: Path) -> dict:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=index,codec_type,width,height,duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode != 0:
            raise ProductionRenderError((result.stderr or "ffprobe failed").strip())
        return json.loads(result.stdout)

    def run(self, user_id: int, item_id: int, job_id: int) -> dict:
        job = self.get(user_id, item_id, job_id)
        approved = self._validate_assets(user_id, item_id)
        # Asset references are immutable: never silently render newer versions.
        for kind, ref in job["approved_asset_refs"].items():
            row = approved.get(kind)
            if not row or row["id"] != ref["asset_id"] or row["version"] != ref["version"]:
                raise ProductionRenderError(
                    f"Approved {kind} version changed; submit a new render job."
                )
        request = job["request"]
        source = Path(request["source_video_path"]).resolve()
        output_dir = self.output_root / str(user_id) / str(item_id) / str(job_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        started_at = job.get("started_at") or time.time()
        self.store.update_production_render_job(
            user_id, item_id, job_id, {"started_at": started_at, "error": None}
        )
        try:
            self._stage(user_id, item_id, job_id, "preparing", 8)
            probe = self._probe(source)
            video_stream = next(
                (row for row in probe.get("streams", []) if row.get("codec_type") == "video"),
                None,
            )
            if not video_stream:
                raise ProductionRenderError("Source has no video stream.")
            source_duration = float((probe.get("format") or {}).get("duration") or 0)
            if source_duration <= 0:
                raise ProductionRenderError("Source video duration is invalid.")

            script_segments = self._section_segments(
                user_id, item_id, approved["script_draft"],
                request.get("source_subtitle_boxes"),
            )
            voice_plan = approved["voice_plan"]["payload"]
            voice_language = self._voice_language(voice_plan)
            self._stage(user_id, item_id, job_id, "tts", 20)
            synthesized = []
            clips_dir = output_dir / "tts"
            clips_dir.mkdir(exist_ok=True)
            for index, segment in enumerate(script_segments):
                clip_path = clips_dir / f"section_{index + 1:03d}.wav"
                if not clip_path.exists() or clip_path.stat().st_size <= 0:
                    self.tts_service.synthesize(
                        segment.text,
                        language=voice_language,
                        voice=self._voice_for_section(index, voice_plan, request),
                        output_path=clip_path,
                    )
                synthesized.append((index, segment, clip_path))
                self._stage(
                    user_id, item_id, job_id, "tts",
                    20 + 28 * ((index + 1) / len(script_segments)),
                )

            scheduler = LocalizationService(
                tts_service=self.tts_service,
                mixer=self.mixer,
                config=LocalizationConfig(tts_max_speed_ratio=1.35),
            )
            clips, playback = scheduler._schedule_tts_clips(
                synthesized, total_duration=max(source_duration, script_segments[-1].end)
            )
            if len(playback) != len(script_segments):
                raise ProductionRenderError("TTS coverage is incomplete.")
            voice_track = output_dir / "voice_timeline.wav"
            total_duration = max(source_duration, playback[-1].end)
            self.mixer.build_dubbed_track(
                clips, total_duration=total_duration, output_path=voice_track
            )

            self._stage(user_id, item_id, job_id, "subtitle", 55)
            subtitle_cues = self.timeline.from_segments(
                playback, audio_duration=total_duration
            )
            subtitle_coverage = validate_timeline_coverage(
                playback, subtitle_cues,
                localized_start_attr="start_time",
                localized_end_attr="end_time",
            )
            if not subtitle_coverage.complete:
                raise ProductionRenderError("Subtitle cue coverage is incomplete.")
            subtitles = output_dir / "subtitles.ass"
            subtitles.write_text(
                self.timeline.generate_ass_karaoke(
                    subtitle_cues,
                    frame_width=int(video_stream.get("width") or 1080),
                    frame_height=int(video_stream.get("height") or 1920),
                ),
                encoding="utf-8",
            )
            subtitle_font_size = max(
                18, min(50, round(int(video_stream.get("height") or 1920) * 0.065))
            )
            maximum_subtitle_width_ratio = max(
                (
                    len(line) * subtitle_font_size * 0.58
                    / max(1, int(video_stream.get("width") or 1080))
                    for cue in subtitle_cues
                    for line in cue.text.split("\n")
                ),
                default=0.0,
            )

            self._stage(user_id, item_id, job_id, "visual_cleanup", 66)
            overlays = []
            for box in request.get("source_subtitle_boxes") or []:
                overlays.append(TextOverlay(
                    start=float(box["start"]), end=float(box["end"]),
                    x=int(box["x"]), y=int(box["y"]),
                    width=int(box["width"]), height=int(box["height"]), text="",
                ))
            geometry = self.renderer.get_cleanup_geometry(
                overlays,
                int(video_stream.get("width") or 1080),
                int(video_stream.get("height") or 1920),
            ) if overlays else []
            if any(not item["cleanup_safe"] for item in geometry):
                # Unsafe cues are disabled by Renderer, and reported in QC.
                pass

            audio_for_render = voice_track
            self._stage(user_id, item_id, job_id, "mixing", 74)
            if request.get("preserve_source_audio"):
                source_audio = output_dir / "source_audio.wav"
                if not source_audio.exists() or source_audio.stat().st_size <= 0:
                    result = subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", str(source_audio)],
                        capture_output=True, text=True, check=False, timeout=300,
                    )
                    if result.returncode != 0:
                        raise ProductionRenderError((result.stderr or "audio extraction failed").strip())
                mixed = output_dir / "mixed_audio.wav"
                self.mixer.mix(AudioMix(source_audio, voice_track, 0.12), mixed)
                audio_for_render = mixed

            self._stage(user_id, item_id, job_id, "rendering", 82)
            output = output_dir / "final.mp4"
            self.renderer.render(
                source, audio_for_render, subtitles=subtitles,
                output_path=output, text_overlays=overlays,
            )

            self._stage(user_id, item_id, job_id, "qc", 96)
            validation = MP4Validator().validate(
                str(output),
                expected_duration=source_duration,
                expected_resolution=(
                    f"{video_stream.get('width')}x{video_stream.get('height')}"
                ),
            )
            output_probe = self._probe(output)
            output_format_duration = float(
                (output_probe.get("format") or {}).get("duration") or 0
            )
            output_video_stream = next(
                (row for row in output_probe.get("streams", [])
                 if row.get("codec_type") == "video"),
                None,
            )
            output_audio_stream = next(
                (row for row in output_probe.get("streams", [])
                 if row.get("codec_type") == "audio"),
                None,
            )
            output_video_duration = float(
                (output_video_stream or {}).get("duration") or output_format_duration
            )
            output_audio_duration = float(
                (output_audio_stream or {}).get("duration") or 0
            )
            av_duration_mismatch = abs(output_video_duration - output_audio_duration)
            av_duration_tolerance = max(0.25, output_video_duration * 0.01)
            max_cleanup_ratio = max(
                (item["height_ratio"] for item in geometry), default=0.0
            )
            qc = {
                "video_exists": output.is_file() and output.stat().st_size > 0,
                "container_status": validation.status.value,
                "duration_seconds": validation.duration_seconds,
                "resolution": validation.resolution,
                "audio_codec": validation.audio_codec,
                "video_duration_seconds": output_video_duration,
                "audio_duration_seconds": output_audio_duration,
                "av_duration_mismatch_seconds": av_duration_mismatch,
                "av_duration_match": bool(
                    output_audio_stream and av_duration_mismatch <= av_duration_tolerance
                ),
                "subtitle_coverage": subtitle_coverage.coverage_ratio,
                "subtitle_readability_safe": maximum_subtitle_width_ratio <= 0.92,
                "maximum_estimated_subtitle_width_ratio": maximum_subtitle_width_ratio,
                "tts_coverage": len(playback) / len(script_segments),
                "start_coverage": bool(
                    playback and playback[0].start <= script_segments[0].start + 0.15
                ),
                "cleanup_geometry_safe": all(
                    item["height_ratio"] <= 0.12 for item in geometry
                ),
                "maximum_cleanup_height_ratio": max_cleanup_ratio,
                "cleanup_regions": geometry,
                "issues": validation.issues,
                "warnings": validation.warnings,
            }
            passed = (
                validation.status == ValidationStatus.VALID
                and qc["video_exists"]
                and qc["subtitle_coverage"] == 1.0
                and qc["av_duration_match"]
                and qc["subtitle_readability_safe"]
                and qc["tts_coverage"] == 1.0
                and qc["start_coverage"]
                and qc["cleanup_geometry_safe"]
            )
            if not passed:
                raise ProductionRenderError("Rendered MP4 failed deterministic QC.")
            self.store.update_production_render_job(
                user_id, item_id, job_id,
                {
                    "status": "completed", "current_stage": "completed",
                    "progress": 100, "output_path": str(output),
                    "qc": qc, "completed_at": time.time(),
                },
            )
            self.store.add_production_event(
                user_id, item_id, event_type="production_render_completed",
                note=f"CP7B render job {job_id} passed QC",
            )
            return self.get(user_id, item_id, job_id)
        except Exception as exc:
            self.store.update_production_render_job(
                user_id, item_id, job_id,
                {
                    "status": "failed", "current_stage": "failed",
                    "error": str(exc)[:2000], "completed_at": time.time(),
                },
            )
            self.store.add_production_event(
                user_id, item_id, event_type="production_render_failed",
                note=f"CP7B render job {job_id}: {str(exc)[:700]}",
            )
            if isinstance(exc, ProductionRenderError):
                raise
            raise ProductionRenderError(str(exc)) from exc

    def resume(self, user_id: int, item_id: int, job_id: int) -> dict:
        job = self.get(user_id, item_id, job_id)
        if job["status"] == "completed":
            return job
        # Existing non-empty section clips are reused by run().
        return self.run(user_id, item_id, job_id)
