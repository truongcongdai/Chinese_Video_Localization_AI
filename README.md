# Universal Video AI

End-to-end video localization platform: download → transcribe → translate → synthesize → mix → render.

## Features

- **Video Download**: Support for multiple platforms (YouTube, TikTok, etc.)
- **Audio Extraction**: High-quality WAV extraction using FFmpeg
- **Audio Separation**: Stem separation (vocals, drums, bass, other) using Demucs
- **Speech-to-Text**: Transcription using Whisper
- **Translation**: Multi-language text translation
- **Text-to-Speech**: Speech synthesis using Edge TTS
- **Subtitles**: Automatic subtitle generation (SRT/VTT formats)
- **Audio Mixing**: Blend original audio with TTS/translated audio
- **End-to-End Orchestration**: Full pipeline in one command

## Installation

```bash
# Clone repository
git clone <repo>
cd Chinese_Video_Localization_AI

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install optional backends
pip install demucs openai-whisper torch torchaudio
pip install edge-tts