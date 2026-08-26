# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for packaging Chinese Video Localization AI into a Windows executable.
Usage: pyinstaller build_exe.spec
"""

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Get the repository root
REPO_ROOT = Path(os.getcwd()).resolve()
SRC_DIR = REPO_ROOT / "src"
WEB_STATIC_DIR = SRC_DIR / "universal_video_ai" / "web" / "static"

block_cipher = None

# Collect all data files
datas = [
    # Web static files (HTML, CSS, JS, images)
    (str(WEB_STATIC_DIR), "universal_video_ai/web/static"),
    
    # Config files
    (str(REPO_ROOT / ".env.example"), "."),
    
    # Database (optional - will be created at runtime if not exists)
    # (str(REPO_ROOT / "database.sqlite3"), "."),
]

# openai-whisper imports its Python package as ``whisper`` and loads the mel
# filter bank from package data at runtime. Hidden imports alone do not copy
# ``whisper/assets/mel_filters.npz`` into PyInstaller's temporary _MEI folder.
whisper_datas = collect_data_files("whisper", includes=["assets/*"])
if not any(Path(source).name == "mel_filters.npz" for source, _destination in whisper_datas):
    raise RuntimeError(
        "PyInstaller cannot find whisper/assets/mel_filters.npz. "
        "Install openai-whisper in the build environment before packaging."
    )
datas += whisper_datas

# Collect hidden imports for heavy dependencies
hiddenimports = [
    # PyTorch
    'torch',
    'torch.nn',
    'torchvision',
    'torchaudio',

    # Diffusers & Transformers
    'diffusers',
    'transformers',
    'accelerate',
    'safetensors',

    # Audio processing
    'librosa',
    'demucs',
    'whisper',
    'edge_tts',

    # OCR
    'easyocr',

    # Web framework
    'uvicorn',
    'fastapi',
    'starlette',

    # Other dependencies
    'yt_dlp',
    'redis',
    'passlib',
    'passlib.handlers',
    'passlib.handlers.bcrypt',
    'passlib.handlers.sha2_crypt',
    'bcrypt',
    'googletrans',
    'mutagen',

    # Universal Video AI modules - collect all submodules
    'universal_video_ai',
    'universal_video_ai.config',
    'universal_video_ai.orchestrator',
    'universal_video_ai.web',
    'universal_video_ai.web.app',
    'universal_video_ai.web.store',
    'universal_video_ai.web.auth',
    'universal_video_ai.web.static',
]
hiddenimports += collect_submodules("whisper")

a = Analysis(
    ['scripts/run_web_wrapper.py'],
    pathex=[str(REPO_ROOT), str(SRC_DIR), str(SRC_DIR / "universal_video_ai")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test modules to reduce size
        'pytest',
        'tests',
        'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ChineseVideoLocalizationAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console to see errors and startup messages
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add path to .ico file if you have one
)
