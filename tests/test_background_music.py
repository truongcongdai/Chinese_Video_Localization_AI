from pathlib import Path

from universal_video_ai.audio.background_music import (
    BackgroundMusicConfig,
    BackgroundMusicLibrary,
)


def test_background_music_library_returns_none_when_empty(tmp_path: Path) -> None:
    library = BackgroundMusicLibrary(BackgroundMusicConfig(library_dir=tmp_path))

    assert library.select("video-a") is None


def test_background_music_library_is_deterministic_and_filters_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not audio", encoding="utf-8")
    (tmp_path / "calm.mp3").write_bytes(b"music")
    (tmp_path / "bright.wav").write_bytes(b"music")
    library = BackgroundMusicLibrary(BackgroundMusicConfig(library_dir=tmp_path))

    first = library.select("same-video")
    second = library.select("same-video")

    assert first == second
    assert first is not None
    assert first.suffix in {".mp3", ".wav"}


def test_background_music_library_can_select_by_reference_audio(tmp_path: Path) -> None:
    reference = tmp_path / "source.wav"
    calm = tmp_path / "calm.mp3"
    bright = tmp_path / "bright.wav"
    reference.write_bytes(b"source")
    calm.write_bytes(b"calm")
    bright.write_bytes(b"bright")
    library = BackgroundMusicLibrary(BackgroundMusicConfig(library_dir=tmp_path))

    class FakeAnalyzer:
        def analyze(self, path: Path):
            return path.name

        def calculate_similarity(self, reference_features, candidate_features):
            return 0.95 if candidate_features == "bright.wav" else 0.25

    library._analyzer = FakeAnalyzer()  # type: ignore[assignment]

    assert library.select_like(reference, selection_key="video-a") == bright
