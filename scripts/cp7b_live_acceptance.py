"""Render a user-supplied representative source through the CP7B media path.

This is an operator acceptance utility, not an automatic downloader. It writes
only to the explicitly supplied output directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from universal_video_ai.content_os.renderer import MP4Validator
from universal_video_ai.mixer.service import MixerService
from universal_video_ai.orchestrator.service import LocalizationConfig, LocalizationService
from universal_video_ai.render.renderer import RenderConfig, Renderer, TextOverlay
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.timeline.service import TimelineService
from universal_video_ai.tts.registry import RegistryTTSBackend
from universal_video_ai.tts.service import TTSService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--voice", default="vi-VN-HoaiMyNeural")
    args = parser.parse_args()
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    segments = [
        TranscriptSegment(0.0, 2.2, "Xin chào, chào mừng bạn đến đây."),
        TranscriptSegment(2.5, 5.0, "Video này đã được bản địa hóa đầy đủ."),
    ]
    tts = TTSService(backend=RegistryTTSBackend())
    mixer = MixerService()
    synthesized = []
    for index, segment in enumerate(segments):
        path = output_dir / f"voice_{index + 1}.wav"
        tts.synthesize(
            segment.text, language="vi", voice=args.voice, output_path=path
        )
        synthesized.append((index, segment, path))

    scheduler = LocalizationService(
        tts_service=tts,
        mixer=mixer,
        config=LocalizationConfig(tts_max_speed_ratio=1.35),
    )
    clips, playback = scheduler._schedule_tts_clips(
        synthesized, total_duration=8.0
    )
    voice_track = output_dir / "voice_timeline.wav"
    mixer.build_dubbed_track(clips, total_duration=8.0, output_path=voice_track)

    timeline = TimelineService()
    subtitle_cues = timeline.from_segments(playback, audio_duration=8.0)
    subtitles = output_dir / "vietnamese.ass"
    subtitles.write_text(
        timeline.generate_ass_karaoke(
            subtitle_cues, frame_width=640, frame_height=360, font_size=28
        ),
        encoding="utf-8",
    )
    overlays = [
        TextOverlay(0.0, 2.2, 180, 286, 280, 34, ""),
        TextOverlay(2.5, 5.0, 180, 286, 280, 34, ""),
    ]
    renderer = Renderer(RenderConfig(preset="veryfast"))
    geometry = renderer.get_cleanup_geometry(overlays, 640, 360)
    final = output_dir / "cp7b-final.mp4"
    renderer.render(
        source, voice_track, subtitles=subtitles,
        output_path=final, text_overlays=overlays,
    )
    validation = MP4Validator().validate(str(final), 8.0, "640x360")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of", "json", str(final),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    report = {
        "source": str(source),
        "output": str(final),
        "voice": args.voice,
        "first_source_event": segments[0].start,
        "first_localized_event": playback[0].start,
        "first_subtitle_cue": subtitle_cues[0].start_time,
        "subtitle_cue_count": len(subtitle_cues),
        "tts_clip_count": len(clips),
        "cleanup_regions": geometry,
        "maximum_cleanup_height_ratio": max(
            item["height_ratio"] for item in geometry
        ),
        "validation": {
            "status": validation.status.value,
            "issues": validation.issues,
            "warnings": validation.warnings,
        },
        "ffprobe": json.loads(probe.stdout),
    }
    report_path = output_dir / "acceptance-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if validation.status.value == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
