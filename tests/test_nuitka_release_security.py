from pathlib import Path


def test_nuitka_pipeline_validates_whisper_asset_before_and_after_build():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    assert "$WhisperAsset" in script
    assert "$BundledWhisperAsset" in script
    assert "whisper\\assets\\mel_filters.npz" in script


def test_nuitka_pipeline_rejects_python_source_and_sensitive_data():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    for extension in (".py", ".pyw", ".pyi", ".pyc", ".pyo"):
        assert f'"{extension}"' in script
    for marker in ("credentials", "id_rsa", "cookies", "web_jobs", "local_data", "\\.git"):
        assert marker in script
    assert '"cacert.pem"' not in script
    assert '^\\.(key|pfx|p12)$' in script


def test_nuitka_pipeline_emits_release_checksum():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    assert "Get-FileHash" in script
    assert '"$Archive.sha256"' in script


def test_nuitka_pipeline_keeps_torch_build_tools_compatible_and_excludes_transformers_cli():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    assert '"setuptools<82"' in script
    assert "--include-package=transformers" in script
    assert "--nofollow-import-to=transformers.cli" in script


def test_nuitka_pipeline_limits_compiler_memory_and_excludes_developer_modules():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    for option in ("--low-memory", "--jobs=1", "--lto=no"):
        assert option in script
    for module in (
        "transformers.testing_utils",
        "diffusers.utils.testing_utils",
        "passlib.tests",
        "easyocr.DBNet.assets.ops.dcn.setup",
        "yt_dlp.extractor.lazy_extractors",
        "pygments.lexers.*",
    ):
        assert f"--nofollow-import-to={module}" in script


def test_nuitka_pipeline_only_caches_successful_preflight_for_unchanged_inputs():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    assert 'Get-ChildItem src, tests, packaging, scripts -Recurse -File' in script
    assert '__pycache__' in script
    assert '\\.egg-info' in script
    assert 'Get-FileHash -Algorithm SHA256' in script
    assert '$CachedPreflightHash -eq $PreflightHash' in script
    assert script.index('if ($LASTEXITCODE -ne 0) {', script.index('& $Python -m pytest -q')) < script.index(
        'Set-Content -LiteralPath $PreflightStamp'
    )


def test_nuitka_pipeline_uses_normal_ytdlp_extractors_and_preserves_incremental_objects():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")
    launcher = Path("packaging/windows_launcher.py").read_text(encoding="utf-8")

    assert "--nofollow-import-to=yt_dlp.extractor.lazy_extractors" in script
    assert 'YTDLP_NO_LAZY_EXTRACTORS", "1"' in launcher
    assert "--remove-output" not in script


def test_nuitka_pipeline_supports_fast_and_package_modes():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")
    launcher = Path("build_nuitka.bat").read_text(encoding="utf-8")

    assert '[ValidateSet("release", "fast", "package")]' in script
    assert '$Mode -eq "package"' in script
    assert '$Mode -eq "fast"' in script
    assert "ZIP skipped" in script
    assert "%*" in launcher


def test_nuitka_pipeline_caches_dependency_installation():
    script = Path("packaging/build_windows.ps1").read_text(encoding="utf-8")

    assert "build-dependencies.sha256" in script
    assert "Dependency cache hit" in script
    assert "pip install --no-deps -e ." in script
    assert '"setup.py"' in script
    assert "Where-Object { Test-Path" in script
    assert "Join-Path $ProjectRoot $_" in script
