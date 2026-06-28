# MODEL_SETUP.md

Developer guide — making Demucs / Whisper / FFmpeg / Edge-TTS available locally
=======================================================================

This document collects short, actionable instructions for preparing a development
machine to run the audio pipeline locally (Demucs, Whisper, FFmpeg, Edge-TTS).

General notes
- Prefer installing packages into a virtualenv (python -m venv .venv; .venv\Scripts\Activate.ps1 on Windows).
- Many packages (Whisper / Demucs) may require `torch`. Use the PyTorch install selector at https://pytorch.org/get-started/locally/ to obtain the correct wheel (CPU vs CUDA).
- The project uses FFmpeg and FFprobe on PATH for audio operations. Demucs needs Python or its CLI.

Ubuntu / Debian
---------------

1) System packages (ffmpeg, build tools)
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg build-essential git