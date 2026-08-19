$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Script này phải chạy trên Windows 10/11 64-bit."
}

$Python = Join-Path $ProjectRoot ".venv-build\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    py -3.10 -m venv .venv-build
}

& $Python -m pip install --upgrade pip wheel setuptools
& $Python -m pip install -r requirements.txt
& $Python -m pip install nuitka ordered-set zstandard
& $Python -m pip install -e .

$FfmpegBin = Join-Path $ProjectRoot "vendor\ffmpeg\bin"
foreach ($Tool in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path (Join-Path $FfmpegBin $Tool))) {
        throw "Thiếu vendor\ffmpeg\bin\$Tool. Hãy đặt bản FFmpeg Windows 64-bit vào thư mục này."
    }
}

$OutputRoot = Join-Path $ProjectRoot "build\windows"
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

& $Python -m nuitka `
    --mode=standalone `
    --assume-yes-for-downloads `
    --enable-plugin=multiprocessing `
    --output-dir=$OutputRoot `
    --output-filename=ChineseVideoAI.exe `
    --include-package=universal_video_ai `
    --include-package=whisper `
    --include-package=yt_dlp `
    --include-package=googletrans `
    --include-package=edge_tts `
    --include-package=easyocr `
    --include-package=demucs `
    --include-package=passlib `
    --include-package=passlib.handlers `
    --include-package-data=whisper `
    --include-package-data=easyocr `
    --include-data-dir=src/universal_video_ai/web/static=universal_video_ai/web/static `
    --nofollow-import-to=pytest `
    --nofollow-import-to=tests `
    --remove-output `
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

# Refuse to ship common secrets and user data accidentally.
$Forbidden = Get-ChildItem $ReleaseDir -Recurse -Force | Where-Object {
    $_.Name -in @(".env", "database.sqlite3") -or
    $_.FullName -match "[\\/](cookies|web_jobs)[\\/]"
}
if ($Forbidden) {
    $Names = ($Forbidden | ForEach-Object FullName) -join "`n"
    throw "Gói build chứa dữ liệu cấm:`n$Names"
}

$Archive = Join-Path $OutputRoot "ChineseVideoAI-Windows-x64.zip"
if (Test-Path $Archive) {
    Remove-Item -Force $Archive
}
Compress-Archive -Path $ReleaseDir -DestinationPath $Archive -CompressionLevel Optimal
Write-Host "Build hoàn tất: $Archive" -ForegroundColor Green
