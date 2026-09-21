from pathlib import Path


def test_pyinstaller_spec_collects_whisper_runtime_assets():
    spec = Path("build_exe.spec").read_text(encoding="utf-8")

    assert 'Path(whisper.__file__).resolve().parent / "assets" / "mel_filters.npz"' in spec
    assert 'datas.append((str(whisper_asset), "whisper/assets"))' in spec
    assert "'whisper'" in spec
    assert "'openai.whisper'" not in spec


def test_pyinstaller_build_preflights_mel_filter_asset():
    script = Path("build_exe.bat").read_text(encoding="utf-8")

    assert "assets'/'mel_filters.npz" in script
    assert "openai-whisper assets are incomplete" in script
