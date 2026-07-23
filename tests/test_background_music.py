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
