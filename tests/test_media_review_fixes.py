"""Offline regressions for review findings F49–F63."""
import asyncio
from dataclasses import replace
import json
import logging
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import wave

import pytest

from universal_video_ai.audio.duration import media_duration
from universal_video_ai.audio.demucs import DemucsProcessor, DemucsOutput
from universal_video_ai.async_runtime import run_blocking_bounded
from universal_video_ai.downloader.rate_limiter import RateLimiter
from universal_video_ai.mixer.service import AudioMix, MixerService, TimedAudioClip
from universal_video_ai.orchestrator.service import LocalizationService, LocalizationConfig
from universal_video_ai.render.renderer import TextOverlay
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.timeline.service import TimelineSegment


def write_wav(path, seconds=1.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x10\x01" * int(8000 * seconds))
    return path


def test_pcm_fast_path_never_probes_valid_riff(tmp_path, monkeypatch):
    path = write_wav(tmp_path / "normal.wav")
    monkeypatch.setattr("subprocess.run", Mock(side_effect=AssertionError("unnecessary probe")))
    assert media_duration(path) == 1.0


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="requires FFprobe")
def test_unknown_wav_length_keeps_real_duration_and_uncompressed_schedule(tmp_path):
    path = write_wav(tmp_path / "stream.wav")
    raw = bytearray(path.read_bytes())
    raw[4:8] = b"\xff" * 4
    raw[40:44] = b"\xff" * 4
    path.write_bytes(raw)
    assert media_duration(path) == 1.0
    assert MixerService()._probe_duration(path) == 1.0
    service = object.__new__(LocalizationService)
    service.config, service.logger = LocalizationConfig(), logging.getLogger(__name__)
    clips, _ = service._schedule_tts_clips([(0, TranscriptSegment(0, 1, "all words"), path)], total_duration=5)
    assert clips[0].audio_path == path
    assert clips[0].end == 1.0


def test_rate_limit_competitors_reserve_quota_after_sleep(monkeypatch):
    clock = [100.0]
    sleep = asyncio.sleep
    monkeypatch.setattr("universal_video_ai.downloader.rate_limiter.time.monotonic", lambda: clock[0])
    async def next_minute(delay):
        clock[0] += delay
        await sleep(0)
    monkeypatch.setattr("universal_video_ai.downloader.rate_limiter.asyncio.sleep", next_minute)
    limiter = RateLimiter(max_requests_per_minute=1, max_concurrent=3, delay_between_requests=0)
    async def scenario():
        async def request():
            await limiter.acquire(7)
            limiter.release()
        await asyncio.gather(request(), request(), request())
        assert list(limiter.global_requests) == list(limiter.user_requests[7])
        assert len(limiter.global_requests) == 1
        assert limiter.active_downloads == 0
    asyncio.run(scenario())


def test_cancel_during_throttle_returns_capacity():
    async def scenario():
        limiter = RateLimiter(max_concurrent=1, delay_between_requests=60)
        task = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        limiter.delay_between_requests = 0
        await asyncio.wait_for(limiter.acquire(), timeout=0.1)
        limiter.release()
        assert limiter.active_downloads == 0
    asyncio.run(scenario())


def test_whisper_cpu_numeric_error_fails_without_recursive_lock(monkeypatch, tmp_path):
    import sys
    from universal_video_ai.speech import whisper as module
    name = "review-cpu-model"
    fake_model = SimpleNamespace(transcribe=Mock(side_effect=ValueError("invalid values in logits")))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=Mock(return_value=fake_model)))
    transcriber = module.WhisperTranscriber(module.WhisperConfig(model=name, device="cpu"))
    # A nonblocking lock makes a regression fail immediately, without hanging pytest.
    class Guard:
        def __init__(self): self.locked = False
        def __enter__(self):
            assert not self.locked, "CPU recovery recursively acquired its inference lock"
            self.locked = True
        def __exit__(self, *args): self.locked = False
    lock = Guard()
    monkeypatch.setattr(module, "_whisper_inference_lock", lambda *args: lock)
    with pytest.raises(RuntimeError, match="invalid values"):
        transcriber._run_whisper(tmp_path / "fake.wav")
    assert fake_model.transcribe.call_count == 1


def test_cleanup_boxes_do_not_count_as_translated_caption():
    cue = TimelineSegment(start_time=0, end_time=1, text="translated")
    cleanup = TextOverlay(start=0, end=1, x=0, y=0, width=100, height=30, text=" ")
    assert LocalizationService._filter_uncovered_subtitle_segments([cue], [cleanup]) == [cue]
    assert LocalizationService._filter_uncovered_subtitle_segments([cue], [replace(cleanup, text="translated")]) == []


def test_blocking_capacity_and_completion_callback_survive_cancellation():
    async def scenario():
        slots = asyncio.Semaphore(1)
        release, started = threading.Event(), threading.Event()
        calls, finished = [], []
        def work(label):
            calls.append(label)
            started.set()
            assert release.wait(2)
        first = asyncio.create_task(run_blocking_bounded(slots, work, "first", on_complete=lambda: finished.append("first")))
        try:
            for _ in range(100):
                if started.is_set(): break
                await asyncio.sleep(.001)
            assert started.is_set()
            first.cancel()
            with pytest.raises(asyncio.CancelledError): await first
            second = asyncio.create_task(run_blocking_bounded(slots, work, "second"))
            await asyncio.sleep(.01)
            assert calls == ["first"] and finished == []
            release.set()
            await second
            assert finished == ["first"] and calls == ["first", "second"]
        finally:
            release.set()
    asyncio.run(scenario())


def test_cancellation_waiting_for_stage_releases_owned_throttle_slot():
    async def scenario():
        slots = asyncio.Semaphore(0)
        finished = Mock()
        work = Mock()
        task = asyncio.create_task(run_blocking_bounded(slots, work, on_complete=finished))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        finished.assert_called_once()
        work.assert_not_called()
    asyncio.run(scenario())


def test_orchestrator_transcription_capacity_survives_cancel(tmp_path, monkeypatch):
    import universal_video_ai.orchestrator.service as module
    source = tmp_path / "input.mp4"; source.write_bytes(b"local input")
    started, release = threading.Event(), threading.Event()
    calls = []
    def process(*args, **kwargs):
        calls.append(1); started.set(); release.wait(2)
        raise RuntimeError("fake transcription finished")
    monkeypatch.setattr(module, "create_audio_pipeline", lambda **kwargs: SimpleNamespace(process=process))
    async def scenario():
        monkeypatch.setattr(module, "_TRANSCRIPTION_SLOTS", asyncio.Semaphore(1))
        service = LocalizationService(downloader=Mock(), config=LocalizationConfig())
        first = asyncio.create_task(service._prepare("file://" + str(source), tmp_path))
        try:
            for _ in range(100):
                if started.is_set(): break
                await asyncio.sleep(.001)
            assert started.is_set()
            first.cancel()
            with pytest.raises(asyncio.CancelledError): await first
            second = asyncio.create_task(service._prepare("file://" + str(source), tmp_path))
            await asyncio.sleep(.01)
            assert len(calls) == 1
            release.set()
            with pytest.raises(RuntimeError, match="fake transcription"):
                await second
        finally:
            release.set()
    asyncio.run(scenario())


def test_demucs_cache_is_content_scoped_validated_and_not_published_on_failure(tmp_path, monkeypatch):
    processor = DemucsProcessor()
    first = write_wav(tmp_path / "first" / "video.wav")
    second = write_wav(tmp_path / "second" / "video.wav", 2)
    calls = []
    def separate(source, output_dir):
        calls.append(source)
        paths = {name: write_wav(output_dir / (name + ".wav"), media_duration(source))
                 for name in ("vocals", "drums", "bass", "other")}
        return DemucsOutput(**paths)
    monkeypatch.setattr(processor, "_separate_uncached", separate)
    cache = tmp_path / "cache"
    a = processor.separate(first, cache)
    assert processor.separate(first, cache) == a and len(calls) == 1
    b = processor.separate(second, cache)
    assert a.vocals != b.vocals and media_duration(b.vocals) == 2
    a.vocals.write_bytes(b"corrupted")
    assert media_duration(processor.separate(first, cache).vocals) == 1 and len(calls) == 3
    # An interrupted run, even with some nonempty files, has no completion receipt.
    third = write_wav(tmp_path / "third.wav", 3)
    def interrupted(source, output_dir):
        write_wav(output_dir / "vocals.wav", 3)
        raise RuntimeError("interrupted")
    monkeypatch.setattr(processor, "_separate_uncached", interrupted)
    completed = set(cache.rglob("complete.json"))
    with pytest.raises(RuntimeError, match="interrupted"):
        processor.separate(third, cache)
    assert set(cache.rglob("complete.json")) == completed
    assert not list((cache / "content-v2").glob(".demucs-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires FFmpeg")
def test_final_mix_keeps_longer_dub_and_localization_rejects_overflow(tmp_path):
    source, dub = write_wav(tmp_path / "source.wav"), write_wav(tmp_path / "dub.wav", 2)
    mixed = MixerService().mix(AudioMix(source, dub), tmp_path / "mixed.wav")
    assert media_duration(mixed) == 2
    with pytest.raises(RuntimeError, match="shorten the translation"):
        LocalizationService._reject_dub_overflow([TimedAudioClip(0, 2, dub)], 1)


def test_music_analysis_is_excerpt_bounded_and_cached_across_instances(tmp_path, monkeypatch):
    from universal_video_ai.audio.analyzer import AudioAnalyzer, AudioFeatures
    import universal_video_ai.audio.analyzer as module
    path = write_wav(tmp_path / "music.wav")
    def analyzer():
        instance = object.__new__(AudioAnalyzer)
        instance._librosa_available = True
        return instance
    calls = []
    def analyze(self, source):
        calls.append(source)
        return AudioFeatures(120, "C", 1, .5, .5, .5, 1)
    monkeypatch.setattr(AudioAnalyzer, "_analyze_with_librosa", analyze)
    analyzer().analyze(path)
    analyzer().analyze(path)
    assert len(calls) == 1
    path.write_bytes(path.read_bytes() + b"new bytes")
    analyzer().analyze(path)
    assert len(calls) == 2
    # Verify the actual decoder call is bounded before any spectral work.
    monkeypatch.undo()
    instance = analyzer()
    def load(*args, **kwargs):
        assert kwargs["duration"] == 30 and kwargs["sr"] == 22050 and kwargs["mono"] is True
        raise RuntimeError("stop after checking bounded decoder")
    instance.librosa = SimpleNamespace(load=load)
    instance._analyze_fallback = lambda source: None
    assert instance._analyze_with_librosa(path) is None


def test_ytdlp_keeps_actual_direct_container_path(tmp_path, monkeypatch):
    from universal_video_ai.downloader.ytdlp_downloader import YTDLPDownloader
    from universal_video_ai.downloader.platform import Platform
    import universal_video_ai.downloader.ytdlp_downloader as module
    actual = tmp_path / "video.webm"; actual.write_bytes(b"video")
    ydl = Mock()
    ydl.__enter__ = Mock(return_value=ydl); ydl.__exit__ = Mock()
    ydl.extract_info.return_value = {"ext": "webm"}
    ydl.prepare_filename.return_value = str(actual)
    monkeypatch.setattr(module.yt_dlp, "YoutubeDL", Mock(return_value=ydl))
    monkeypatch.setattr(module, "_cookiefile_for", lambda platform: None)
    monkeypatch.setattr(module, "_cookies_from_browser_for", lambda platform: None)
    result = YTDLPDownloader(Platform.GENERIC).download("https://example.invalid", tmp_path)
    assert result.video_path == actual and result.extension == "webm"


def test_oversized_premium_tts_is_rejected_before_transport(tmp_path, monkeypatch):
    from universal_video_ai.tts.premium import OpenAITTSBackend
    from universal_video_ai.tts.exceptions import SynthesisError
    post = Mock(side_effect=AssertionError("must not contact provider"))
    monkeypatch.setattr("universal_video_ai.tts.premium.post_with_retry", post)
    with pytest.raises(SynthesisError, match="4096"):
        OpenAITTSBackend("fake").synthesize("x" * 4097, tmp_path / "voice.wav")
    post.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_google_owned_client_closes_on_every_terminal_path(monkeypatch, outcome):
    from universal_video_ai.translate.translator import TranslatorFactory, TranslatorConfig, TranslationError
    clients = []
    class Client:
        def __init__(self, **kwargs):
            self.closed = False; clients.append(self)
        async def post(self, *args, **kwargs):
            if outcome == "cancel": raise asyncio.CancelledError()
            if outcome == "error": raise TranslationError("bad response")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [[["translated"]]])
        async def aclose(self): self.closed = True
    monkeypatch.setattr("httpx.AsyncClient", Client)
    async def scenario():
        translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
        if outcome == "success":
            assert await translator.translate("source", "en", "vi") == "translated"
        else:
            with pytest.raises(asyncio.CancelledError if outcome == "cancel" else TranslationError):
                await translator.translate("source", "en", "vi")
    asyncio.run(scenario())
    assert len(clients) == 1 and clients[0].closed


def test_voice_profile_returns_complete_locale_voice(tmp_path):
    from universal_video_ai.voice.clone import VoiceCloner
    cloner = VoiceCloner(tmp_path / "voices")
    profile = cloner.create_profile(1, "sample", write_wav(tmp_path / "sample.wav"), gender="male")
    assert cloner.match_tts_voice(profile.id, "vi-VN").startswith("vi-VN-")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires FFmpeg")
@pytest.mark.parametrize("split", [False, True])
def test_real_transform_split_and_speed_preserve_audio_clock(tmp_path, split):
    from universal_video_ai.postprocess.video_transform import TransformConfig, VideoTransformer
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=160x96:r=30:d=2", "-i", str(write_wav(tmp_path / "audio.wav", 2)),
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True, timeout=15)
    image = tmp_path / "image.ppm"; image.write_bytes(b"P6\n160 96\n255\n" + b"\xff\x00\x00" * 160 * 96)
    config = TransformConfig(enable_split_screen=split, overlay_image_path=image,
                             enable_randomization=True, speed_factor=2)
    output = tmp_path / "result.mp4"
    assert VideoTransformer(config).transform(source, output)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                           check=True, capture_output=True, text=True, timeout=15)
    streams = json.loads(probe.stdout)["streams"]
    video, audio = (next(row for row in streams if row["codec_type"] == kind) for kind in ("video", "audio"))
    assert abs(float(video["duration"]) - 1) < .12
    assert abs(float(video["duration"]) - float(audio["duration"])) < .12
    assert (video["width"], video["height"]) == (160, 96)
