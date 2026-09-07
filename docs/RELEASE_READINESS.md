# CP0-CP11 Release Readiness

This document is the release path for the CP0-CP11 product baseline. It does
not define another checkpoint.

## Product scope

The product supports the direct localization flow (download/import, probe,
ASR, OCR, translation/adaptation, TTS, subtitles, bounded original-subtitle
cleanup, render, and QC) and the Channel Agent flow (research, competitor
intelligence, opportunities, production planning, approved assets, render,
publishing, analytics, learning, and policy-gated autonomous cycles).

Competitor media is metadata-only research evidence. It is never an approved
production asset by default. `research_only`, `needs_review`, and `cleared`
remain distinct rights states, and public publishing requires cleared rights.

## Installation and startup

Use Python 3.11 or newer and a project virtual environment.

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python scripts\run_web.py
```

Ubuntu:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/run_web.py
```

Open `http://127.0.0.1:8080`. Relative paths in `.env` are resolved from the
project working directory. Do not copy a Windows `.env` containing drive-letter
paths to Ubuntu.

FFmpeg and ffprobe must be on `PATH` for media processing. `yt-dlp` is supplied
by `requirements.txt`. Redis is optional for a single local process; the cache
falls back to process memory. Ollama is optional unless local Content Brain or
local LLM generation is enabled. Edge TTS requires `edge-tts` and network
access; optional provider packages and keys are required only when their modes
are selected.

## Configuration classes

- Required: `WEB_SESSION_SECRET`, writable `WEB_DB_PATH`, `TEMP_DIR`, and
  `OUTPUT_DIR`.
- Optional local services: `REDIS_URL`, `OLLAMA_BASE_URL`,
  `CHANNEL_AGENT_OLLAMA_ENABLED`, and `PIPER_VOICE_DIR`.
- Live-only: Google/YouTube OAuth, Meta app settings, external provider keys,
  and `PROVIDER_EXECUTION_MODE=LIVE` with `RUN_LIVE_TESTS=1`.
- Test-only: normal `pytest` needs no provider credentials and forces MOCK mode
  unless the live opt-in is explicitly present.

Never put access tokens, refresh tokens, OAuth client secrets, cookies, or real
API keys in tracked files. Per-user OAuth tokens belong in the configured
database and are never returned by status/list APIs.

## Provider execution modes

- `MOCK`: deterministic fixtures only; no live fallback.
- `CACHE`: use a secret-redacted cache key; a miss uses a supplied mock unless
  live opt-in is explicit.
- `DRY_RUN`: describe the intended provider operation without executing it.
- `LIVE`: blocked unless `RUN_LIVE_TESTS=1`; set budgets and use private/test
  destinations before enabling it.

Normal regression:

```bash
pytest -q
python -m compileall -q src tests
node --check src/universal_video_ai/web/static/app.js
```

The pytest terminal report must show `LIVE_PROVIDER_CALLS=0`.

## Channel download identity and retries

Canonical identity is owner + platform + external video ID. A changed YouTube
URL form does not create a second source. Preflight classifies NEW, QUEUED,
PROCESSING, SUCCESS, retryable FAILED, terminal FAILED, and SKIPPED. Execution
downloads NEW once, retries only eligible failures, and skips queued,
processing, and successful sources. History keeps one logical source row with
nested attempts.

## Queue reruns

`Run all again` uses `RESTART_FROM_BEGINNING`, preserves the logical item and
configuration history, creates a new execution attempt, and requires explicit
confirmation for completed work. `Retry from failed stage` preserves valid
earlier outputs. Remote publishing is never repeated unless a separately
authorized publishing action explicitly requests it.

## Autonomous operator

`FULL_AUTONOMOUS` persists owner-scoped configuration, schedule, cycle,
progress, outputs, cost report, and provider mode. Approval gates default on.
Only gates disabled in an explicit owner policy may be bypassed. Hourly, daily,
and weekly schedules are timezone-aware. Missed-run policies are `run_once`,
`skip`, and `reschedule`; idempotency prevents missed-run bursts. Download,
render, publish, and cycle concurrency remain bounded.

Pause disables new scheduled cycles. Resume re-enables the schedule. Stop after
current prevents another scheduled cycle. After restart, resume the persisted
cycle; completed stages and idempotent output references are reused.

## YouTube, Facebook, Ollama, and TTS

YouTube requires a Google Web OAuth client and the redirect URI documented in
`.env.example`. Facebook requires a Meta Business app, Page selection, and the
required App Review permissions for non-test publishing. Keep dry-run/private
publishing until rights, account, and destination are confirmed.

Ollama is local and optional; install the configured model manually. The app
does not silently pull one. Vietnamese Edge voices are
`vi-VN-HoaiMyNeural` and `vi-VN-NamMinhNeural`. Rate or pitch variants are not
represented as distinct speakers. Unavailable local/provider voices are not
advertised as verified.

## Database backup and restore

Stop the web process before copying SQLite files. Back up the database and any
owned/licensed asset directories together. Test upgrades on a copy first;
initialization is additive and repeatable. Restore by stopping the app,
restoring the matched database/assets backup, checking file ownership and
permissions, then starting the app and running `PRAGMA integrity_check` on a
copy if corruption is suspected.

## Troubleshooting

- Redis unavailable: acceptable for one local process; install/start Redis for
  shared cache behavior.
- FFmpeg or ffprobe missing: install both and add their directory to `PATH`.
- yt-dlp/provider failure: update only inside the virtual environment and retry
  a controlled source.
- Ollama unavailable: start `ollama serve`, verify the configured model, or
  disable local Channel Agent LLM work.
- LIVE blocked: this is intentional; set both explicit mode and live opt-in,
  then verify budgets and private/test destinations.
- OAuth failure: verify redirect URI, app credentials, granted scopes, and
  per-user connection status without logging tokens.
- Failed autonomous cycle: inspect the persisted failed step and cost report,
  correct the underlying dependency, then resume/retry; do not create a second
  logical cycle for the same idempotency key.

## Platform validation boundary

Source paths, subprocess argument lists, `pathlib`, temporary directories,
SQLite, Redis, Ollama URLs, FFmpeg, and yt-dlp are portable across Windows and
Ubuntu. A source audit is not a live Ubuntu test. Run the checklist on the
actual Ubuntu host before production acceptance.
