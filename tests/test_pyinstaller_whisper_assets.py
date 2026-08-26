from pathlib import Path


def test_pyinstaller_spec_collects_whisper_runtime_assets():
    spec = Path("build_exe.spec").read_text(encoding="utf-8")

    assert 'collect_data_files("whisper", includes=["assets/*"])' in spec
    assert 'Path(source).name == "mel_filters.npz"' in spec
    assert "'whisper'" in spec
    assert "'openai.whisper'" not in spec


def test_pyinstaller_build_preflights_mel_filter_asset():
    script = Path("build_exe.bat").read_text(encoding="utf-8")

    assert "assets'/'mel_filters.npz" in script
    assert "openai-whisper assets are incomplete" in script
