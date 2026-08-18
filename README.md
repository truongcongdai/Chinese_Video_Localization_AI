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
- **Animated Subtitles**: 8 subtitle animation effects (fade, karaoke, glow, gradient, bounce, typewriter, wave)
- **Audio Mixing**: Blend original audio with TTS/translated audio
- **Copyright-safe audio**: Automatically replace the downloaded soundtrack
  with licensed local music, loop it to the video duration, and duck it under
  translated speech. Put licensed tracks in `local_data/music` and configure
  `COPYRIGHT_SAFE_AUDIO`, `LICENSED_MUSIC_DIR`, and
  `REPLACEMENT_MUSIC_VOLUME` in `.env`.
- **End-to-End Orchestration**: Full pipeline in one command
- **AI Script Generation**: Advanced script generation with multiple AI providers (Gemini, OpenAI, Ollama, OpenRouter)
- **Video Templates**: Preset video effects with color grading and transitions
- **Queue Management**: Batch processing with priority and concurrency control
- **Statistics Dashboard**: Real-time job statistics and progress tracking
- **Video Presets**: Save and load custom video configuration presets
- **Trend Scanner**: Discover trending content across platforms with optional Agent-Reach integration

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

# Install OpenAI for GPT-4 integration (optional)
pip install openai

# Install Agent-Reach for Trend Scanner (optional)
pip install https://github.com/Panniantong/agent-reach/archive/main.zip
agent-reach install --env=auto --safe
agent-reach doctor
```

## Configuration

### Quick Setup (Recommended)

For the web UI, use the auto-generated environment setup:

```bash
cd dist
python generate_env.py
# Edit .env to add your API keys if needed
python run_web.py
```

This script automatically generates secure random values for `WEB_SESSION_SECRET` and other sensitive fields. You only need to fill in optional fields like SMTP credentials and API keys.

### Manual Setup

Create a `.env` file in the project root or `dist/` folder:

```env
# Web UI (REQUIRED for login sessions)
WEB_SESSION_SECRET=<generate with: openssl rand -hex 32>

# AI Providers (choose one or more)
GEMINI_API_KEY=your_gemini_api_key
OPENAI_API_KEY=your_openai_api_key
OLLAMA_BASE_URL=http://127.0.0.1:11434
OPENROUTER_API_KEY=your_openrouter_api_key

# Default AI Provider for script generation
CREATOR_AI_PROVIDER=gemini  # gemini | openai | ollama | openrouter | auto

# Video/Audio Settings
WEB_RENDER_PRESET=medium
WEB_RENDER_TIMEOUT_SECONDS=1800
COPYRIGHT_SAFE_AUDIO=true
LICENSED_MUSIC_DIR=local_data/music
REPLACEMENT_MUSIC_VOLUME=0.3
```

## Running the Web UI

```bash
python scripts/run_web.py
```

The web UI will be available at `http://localhost:8000`

## API Endpoints

### Authentication
- `POST /api/register` - Register new user
- `POST /api/login` - Login with username/password
- `POST /api/logout` - Logout

### Jobs
- `POST /api/jobs` - Create new localization job
- `GET /api/jobs` - List user's jobs
- `GET /api/jobs/{job_id}` - Get job details
- `DELETE /api/jobs/{job_id}` - Delete job
- `PUT /api/jobs/{job_id}/segments` - Update subtitle segments
- `POST /api/jobs/{job_id}/render` - Render final video

### AI Script Generation
- `POST /api/creator/suggestions` - Generate script with AI
  - Body: `{"topic": "...", "target_language": "vi", "provider": "gemini", "advanced_options": {...}}`
  - Providers: `gemini`, `openai`, `ollama`, `openrouter`, `auto`
  - Advanced options: `style`, `tone`, `sentence_length`, `detail_level`, `custom_instructions`

### TTS
- `POST /api/tts/synthesize` - Synthesize text to speech
  - Body: `{"text": "...", "language": "vi", "voice": "...", "rate": "+0%", "pitch": "+0Hz"}`
- `GET /api/voices?language=vi` - List available voices

### Video Presets
- `GET /api/presets` - List user's video presets
- `POST /api/presets` - Create new preset
- `GET /api/presets/{preset_id}` - Get preset details
- `PUT /api/presets/{preset_id}` - Update preset
- `DELETE /api/presets/{preset_id}` - Delete preset

### Languages
- `GET /api/languages` - List supported languages

## Advanced Features

### Animated Subtitles
Enable animated subtitle effects in the job creation:
```json
{
  "animated_subtitle_config": {
    "enabled": true,
    "effect": "karaoke",
    "style": {
      "font_size": 24,
      "font_color": "white",
      "background_color": "black@0.5"
    },
    "effect_params": {
      "duration_per_char": 0.08
    }
  }
}
```

Available effects: `none`, `fade_in`, `fade_out`, `karaoke`, `glow`, `gradient`, `bounce`, `typewriter`, `wave`

### Video Templates
Apply video templates for consistent styling:
```json
{
  "video_template_config": {
    "enabled": true,
    "template": "cinematic",
    "transition": "fade",
    "color_effect": "warm",
    "audio_filters": {
      "equalizer": {"bass": 2, "treble": 1},
      "compressor": {"threshold": -20, "ratio": 4},
      "normalize": true
    },
    "video_quality": "high"
  }
}
```

Templates: `minimal`, `cinematic`, `vibrant`, `professional`, `social`
Color effects: `none`, `warm`, `cool`, `vintage`, `high_contrast`
Video quality: `low`, `medium`, `high`, `ultra`

### Queue Management
Control batch processing:
```json
{
  "priority": "high",
  "max_concurrent": 3
}
```

### Advanced AI Script Options
Customize AI script generation:
```json
{
  "advanced_options": {
    "style": "educational",
    "tone": "formal",
    "sentence_length": "medium",
    "detail_level": "detailed",
    "custom_instructions": "Focus on practical examples"
  }
}
```

Styles: `general`, `entertaining`, `educational`, `storytelling`, `tutorial`, `review`, `news`, `motivational`
Tones: `neutral`, `casual`, `formal`, `humorous`, `inspiring`, `urgent`
Sentence length: `short`, `medium`, `long`
Detail level: `minimal`, `standard`, `detailed`, `comprehensive`

## Troubleshooting

### FFmpeg not found
Install FFmpeg and add it to your PATH:
- Windows: Download from https://ffmpeg.org/download.html
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

### OpenAI API errors
Ensure you have set `OPENAI_API_KEY` in your `.env` file and have credits in your OpenAI account.

### Ollama connection errors
Make sure Ollama is running: `ollama serve`
Check the base URL in `.env`: `OLLAMA_BASE_URL=http://127.0.0.1:11434`

## License

See LICENSE file for details.
