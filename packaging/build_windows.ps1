param(
    [ValidateSet("release", "fast", "package")]
    [string]$Mode = "release"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Script này phải chạy trên Windows 10/11 64-bit."
}

$Python = Join-Path $ProjectRoot ".venv-build\Scripts\python.exe"
$OutputRoot = Join-Path $ProjectRoot "build\windows"
$ReleaseDir = Join-Path $OutputRoot "ChineseVideoAI"
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

if ($Mode -eq "package") {
    if (-not (Test-Path (Join-Path $ReleaseDir "ChineseVideoAI.exe"))) {
        throw "No existing build to package: $ReleaseDir"
    }
    Write-Host "Package mode: reusing existing build at $ReleaseDir"
}

if ($Mode -ne "package") {
if (-not (Test-Path $Python)) {
    py -3.10 -m venv .venv-build
}

$FfmpegBin = Join-Path $ProjectRoot "vendor\ffmpeg\bin"
# Prefer vendored tools for reproducible releases, but reuse a complete
# system FFmpeg installation when both executables are already on PATH.
if (-not (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe")) -or
    -not (Test-Path (Join-Path $FfmpegBin "ffprobe.exe"))) {
    $FfmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    $FfprobeCommand = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if ($FfmpegCommand -and $FfprobeCommand) {
        $ResolvedFfmpegBin = Split-Path -Parent $FfmpegCommand.Source
        $ResolvedFfprobeBin = Split-Path -Parent $FfprobeCommand.Source
        if ($ResolvedFfmpegBin -eq $ResolvedFfprobeBin) {
            $FfmpegBin = $ResolvedFfmpegBin
            Write-Host "Using FFmpeg from PATH: $FfmpegBin"
        }
    }
}
foreach ($Tool in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path (Join-Path $FfmpegBin $Tool))) {
        throw "Thiếu vendor\ffmpeg\bin\$Tool. Hãy đặt bản FFmpeg Windows 64-bit vào thư mục này."
    }
}

# Torch 2.11 requires setuptools<82. Keep the build toolchain inside that
# constraint instead of upgrading to an incompatible version and immediately
# downgrading it again while installing runtime requirements.
$DependencyStamp = Join-Path $OutputRoot "build-dependencies.sha256"
$DependencyFiles = @(
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg"
) | Where-Object { Test-Path -LiteralPath (Join-Path $ProjectRoot $_) }
$DependencyManifest = ($DependencyFiles | ForEach-Object {
    $DependencyPath = Join-Path $ProjectRoot $_
    "$_|$((Get-FileHash -Algorithm SHA256 -LiteralPath $DependencyPath).Hash)"
}) -join "`n"
$DependencySha256 = [Security.Cryptography.SHA256]::Create()
try {
    $DependencyHash = [BitConverter]::ToString(
        $DependencySha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($DependencyManifest))
    ).Replace("-", "")
} finally {
    $DependencySha256.Dispose()
}
$CachedDependencyHash = if (Test-Path $DependencyStamp) {
    (Get-Content $DependencyStamp -Raw).Trim()
} else { "" }
if ($CachedDependencyHash -ne $DependencyHash) {
    Write-Host "Dependency files changed; synchronizing build environment."
    & $Python -m pip install --upgrade pip wheel "setuptools<82"
    & $Python -m pip install -r requirements.txt
    & $Python -m pip install -r requirements-dev.txt
    & $Python -m pip install nuitka ordered-set zstandard
    if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies." }
    Set-Content -LiteralPath $DependencyStamp -Value $DependencyHash -Encoding ascii
} else {
    Write-Host "Dependency cache hit: requirements unchanged."
}
# Refresh only this project's editable metadata without resolving the complete
# dependency graph on every build.
& $Python -m pip install --no-deps -e .

# Fail before the expensive compile if Whisper's runtime data is incomplete.
$WhisperAsset = & $Python -c "import pathlib, whisper; print(pathlib.Path(whisper.__file__).resolve().parent / 'assets' / 'mel_filters.npz')"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $WhisperAsset)) {
    throw "Missing whisper/assets/mel_filters.npz in the build environment. Reinstall openai-whisper."
}
Write-Host "Whisper asset verified: $WhisperAsset"

$PreflightData = Join-Path $OutputRoot "preflight-data"
$env:TEMP_DIR = $PreflightData
$env:WEB_DB_PATH = Join-Path $PreflightData "database.sqlite3"
$env:YOUTUBE_RESEARCH_ENABLED = "false"
$env:CONTENT_OS_ENABLED = "false"
$env:PATH = "$FfmpegBin;$env:PATH"
& $Python -m compileall -q src packaging scripts
if ($LASTEXITCODE -ne 0) {
    throw "Python compile preflight thất bại."
}

# Cache a successful test preflight by the exact contents of source, tests and
# release configuration. Failed runs never write the stamp, and any relevant
# edit automatically invalidates it. This keeps retries after a Nuitka-only
# failure fast without weakening release verification.
$PreflightStamp = Join-Path $OutputRoot "preflight-tests.sha256"
$PreflightFiles = @(
    Get-ChildItem src, tests, packaging, scripts -Recurse -File | Where-Object {
        $_.Extension -notin @(".pyc", ".pyo") -and
        $_.FullName -notmatch "[\\/]__pycache__[\\/]" -and
        $_.FullName -notmatch "[\\/][^\\/]+\.egg-info[\\/]"
    }
    Get-Item requirements.txt, requirements-dev.txt, build_nuitka.bat
) | Sort-Object FullName -Unique
$PreflightManifest = ($PreflightFiles | ForEach-Object {
    $RelativePath = $_.FullName.Substring($ProjectRoot.Length).TrimStart([char]'\')
    "$RelativePath|$((Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash)"
}) -join "`n"
$Sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $PreflightHash = [BitConverter]::ToString(
        $Sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($PreflightManifest))
    ).Replace("-", "")
} finally {
    $Sha256.Dispose()
}
$CachedPreflightHash = if (Test-Path -LiteralPath $PreflightStamp) {
    (Get-Content -LiteralPath $PreflightStamp -Raw).Trim()
} else {
    ""
}
if ($CachedPreflightHash -eq $PreflightHash) {
    Write-Host "Release preflight cache hit: source and tests unchanged."
} else {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "Release preflight tests thất bại."
    }
    Set-Content -LiteralPath $PreflightStamp -Value $PreflightHash -Encoding ascii
}

$PlaywrightCache = Join-Path $ProjectRoot "build\playwright-browsers-cache"
$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightCache
& $Python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Không tải được Chromium dùng cho tính năng Douyin/browser."
}

# Transformers' developer CLI is not used at runtime. Nuitka 4.1.3's
# transformers plugin corrupts an f-string while rewriting this module;
# nofollow overrides include-package and avoids compiling only that CLI.
& $Python -m nuitka `
    --mode=standalone `
    --assume-yes-for-downloads `
    --enable-plugin=multiprocessing `
    --low-memory `
    --jobs=1 `
    --lto=no `
    --output-dir=$OutputRoot `
    --output-filename=ChineseVideoAI.exe `
    --python-flag=no_docstrings `
    --include-package=universal_video_ai `
    --include-package=whisper `
    --include-package=yt_dlp `
    --include-package=googletrans `
    --include-package=google.genai `
    --include-package=edge_tts `
    --include-package=easyocr `
    --include-package=demucs `
    --include-package=diffusers `
    --include-package=transformers `
    --include-package=accelerate `
    --include-package=safetensors `
    --include-package=playwright `
    --include-package=passlib `
    --include-package=passlib.handlers `
    --include-package-data=whisper `
    --include-package-data=easyocr `
    --include-package-data=playwright `
    --include-data-dir=src/universal_video_ai/web/static=universal_video_ai/web/static `
    --nofollow-import-to=transformers.cli `
    --nofollow-import-to=transformers.testing_utils `
    --nofollow-import-to=passlib.tests `
    --nofollow-import-to=easyocr.DBNet.assets.ops.dcn.setup `
    --nofollow-import-to=yt_dlp.extractor.lazy_extractors `
    --nofollow-import-to=diffusers.utils.testing_utils `
    --nofollow-import-to=pygments.lexers.* `
    --nofollow-import-to=pytest `
    --nofollow-import-to=tests `
    packaging/windows_launcher.py

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build thất bại với mã $LASTEXITCODE."
}

$DistDir = Join-Path $OutputRoot "windows_launcher.dist"
$ReleaseDir = Join-Path $OutputRoot "ChineseVideoAI"
if (Test-Path $ReleaseDir) {
    Remove-Item -Recurse -Force $ReleaseDir
}
Move-Item $DistDir $ReleaseDir

New-Item -ItemType Directory -Force -Path (Join-Path $ReleaseDir "ffmpeg\bin") | Out-Null
Copy-Item (Join-Path $FfmpegBin "ffmpeg.exe") (Join-Path $ReleaseDir "ffmpeg\bin\ffmpeg.exe")
Copy-Item (Join-Path $FfmpegBin "ffprobe.exe") (Join-Path $ReleaseDir "ffmpeg\bin\ffprobe.exe")
Copy-Item (Join-Path $PSScriptRoot "start_windows.bat") (Join-Path $ReleaseDir "Start ChineseVideoAI.bat")
Copy-Item $PlaywrightCache (Join-Path $ReleaseDir "playwright-browsers") -Recurse
}

# Validate the actual release tree, not only the build environment.
$BundledWhisperAsset = Join-Path $ReleaseDir "whisper\assets\mel_filters.npz"
if (-not (Test-Path -LiteralPath $BundledWhisperAsset)) {
    throw "Nuitka release is missing whisper\assets\mel_filters.npz: $BundledWhisperAsset"
}

# Backend Python must be native-compiled. Browser JS/CSS/HTML remains visible
# by design because a browser must receive those files.
$LeakedPython = Get-ChildItem $ReleaseDir -Recurse -Force -File | Where-Object {
    $_.Extension -in @(".py", ".pyw", ".pyi", ".pyc", ".pyo")
}
if ($LeakedPython) {
    $Names = ($LeakedPython | ForEach-Object FullName) -join "`n"
    throw "Python source/bytecode leaked into the release:`n$Names"
}

# Refuse to ship common secrets and user data accidentally.
$Forbidden = Get-ChildItem $ReleaseDir -Recurse -Force | Where-Object {
    $_.Name -match "(?i)^(\.env($|\.)|database.*\.(sqlite|sqlite3|db)|credentials.*\.json|secrets?.*|id_rsa.*)$" -or
    $_.Extension -match "(?i)^\.(key|pfx|p12)$" -or
    ($_.Extension -match "(?i)^\.pem$" -and $_.Name -match "(?i)(private|secret|credential|id[_-])") -or
    $_.FullName -match "(?i)[\\/](cookies|web_jobs|local_data|\.git)[\\/]"
}
if ($Forbidden) {
    $Names = ($Forbidden | ForEach-Object FullName) -join "`n"
    throw "Gói build chứa dữ liệu cấm:`n$Names"
}

if ($Mode -eq "fast") {
    Write-Host "Fast build ready (ZIP skipped): $ReleaseDir" -ForegroundColor Green
    exit 0
}

$Archive = Join-Path $OutputRoot "ChineseVideoAI-Windows-x64.zip"
if (Test-Path $Archive) {
    Remove-Item -Force $Archive
}
Compress-Archive -Path $ReleaseDir -DestinationPath $Archive -CompressionLevel Optimal
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
Set-Content -LiteralPath "$Archive.sha256" -Value "$ArchiveHash  $(Split-Path $Archive -Leaf)" -Encoding ascii
Write-Host "SHA-256: $ArchiveHash"
Write-Host "Build hoàn tất: $Archive" -ForegroundColor Green
