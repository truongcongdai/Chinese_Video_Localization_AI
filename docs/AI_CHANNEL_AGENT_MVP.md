# AI Channel Agent MVP

## Purpose

AI Channel Agent is an opt-in personal/development subsystem. CP0 established
its boundaries and deterministic ranking primitives; CP1 adds authenticated,
read-only analysis of the current user's own YouTube channel. It does not
generate content, schedule work, or publish.

The first experiment may research Vietnamese faceless long-form niches, but no
niche is hardcoded in the implementation.

## Current repository architecture

- **Application package:** `src/universal_video_ai`, using a src layout and
  Python 3.10+ metadata in `setup.py`.
- **Web:** FastAPI application at `src/universal_video_ai/web/app.py`, launched
  by `python scripts/run_web.py` through Uvicorn. Most endpoints live in the
  main app module; Content OS and CP0 use `APIRouter`. The frontend is static
  HTML/CSS/JavaScript under `src/universal_video_ai/web/static`.
- **Localization pipeline:** `orchestrator/factory.py` constructs the injected
  download, audio extraction/Demucs, Whisper transcription, translation,
  timeline/subtitle, TTS, mixing, OCR/text-cover, and FFmpeg rendering services.
  `orchestrator/service.py` coordinates those phases.
- **Persistence:** SQLite is used in two established patterns. The bot/core
  `DatabaseManager` has explicit numbered migrations (currently including
  additive YouTube research tables). The FastAPI `Store` owns the web users,
  jobs, OAuth connections, publishing, scheduling, and Content OS schema.
  CP0 adds no table or migration because status and pure metrics need none.
- **Background work:** the web app uses asyncio tasks, `asyncio.to_thread`,
  concurrency guards, and a 15-second scheduled-publish loop. The `jobs`
  package also provides queue/worker abstractions with optional Redis and local
  fallback behavior. CP0 starts no task or worker.
- **Existing research:** `analytics/youtube_research` already provides sample-
  based trend, competition, and opportunity analysis. `trends` provides an
  optional Agent-Reach scanner. The `web/youtube_research.py` router exists but
  is not included by the main FastAPI app at this checkpoint.
- **Packaging/deployment:** the repository has PyInstaller and Nuitka Windows
  build paths, a standalone launcher, Docker/Docker Compose, and separate
  commercial license/telemetry tests. The current checkout is missing the
  `license_server.server` and `license_server.user_management_server` source
  modules imported by the telemetry test.

## Existing YouTube functionality

- `downloader/youtube.py` uses yt-dlp for downloads, audio extraction,
  subtitles, thumbnails, and public metadata.
- `downloader/channel.py` and web channel-download endpoints enumerate channel
  video candidates for the localization workflow.
- `web/oauth.py` implements per-user Google OAuth for existing social publishing
  and stores tokens through the web Store. Its scope includes upload and
  readonly access.
- `social/youtube.py` uploads private videos through direct YouTube REST calls
  and can also use legacy shared refresh-token environment variables.
- No `googleapiclient` dependency is installed. CP1 uses the existing
  `requests` stack for YouTube Data and Analytics API reads.

The existing publishing OAuth is not silently treated as a CP0 research
connection. CP1 must decide how to reuse it without changing upload behavior or
the personal/commercial boundary.

## Reused components

CP0 reuses the existing centralized environment loader and boolean parser,
FastAPI router convention, application bootstrap payload, and static feature-tab
UI. Later Channel Agent code may call existing localization, YouTube metadata,
YouTube research, and Content OS services. Those existing services do not import
Channel Agent.

## Isolation boundary

```text
web adapter -> channel_agent
                    |
                    +-> may reuse existing services in later checkpoints

existing localization / commercial licensing / Windows packaging
                    X no dependency on channel_agent
```

`channel_agent` is imported only by its web adapter and its own tests. The
localization orchestrator, stores, jobs, publishing code, license code, and build
scripts are unchanged.

## Feature flag

```env
AI_CHANNEL_AGENT_ENABLED=false
```

The default is false. `universal_video_ai.config.is_ai_channel_agent_enabled()`
is the centralized reader. The status endpoint remains safe while disabled; the
frontend navigation item starts hidden and is only revealed when the bootstrap
payload reports the feature enabled.

## CP0 architecture

```text
channel_agent/
├── analytics.py   # snapshot velocity, engagement, outlier, weighted score
├── models.py      # source metadata, metric snapshot, rights state
├── providers.py   # provider-neutral AIProvider protocol
├── service.py     # side-effect-free status service
└── youtube.py     # read-only CP1 service protocols

web/channel_agent_router.py
└── GET /api/channel-agent/status
```

The status response contains the flag, MVP version, Channel Agent YouTube
connection state, and Ollama availability. CP0 makes no network probe, so Ollama
availability is `null` (unknown), and it does not claim the existing publishing
OAuth is a Channel Agent connection.

No CP0 persistence model is needed. `SourceMetadata` and `VideoMetricSnapshot`
define the minimum platform-neutral input boundary without prematurely adding
tables already adjacent to existing YouTube research and Content OS schemas.

## Trend scoring

CP0 implements these pure functions:

- `view_velocity = max(current_views - previous_views, 0) / elapsed_hours`
- `engagement_rate = (likes + comments) / views`
- `outlier_ratio = video_views / channel_typical_views`
- `trend_score = 0.30 velocity + 0.25 outlier + 0.20 engagement + 0.15 freshness + 0.10 competition_opportunity`

The five score inputs use a common 0.0–1.0 range and are clamped. Missing,
negative, NaN, and infinite inputs cannot produce NaN or infinity. Velocity
requires timezone-aware timestamps and safely returns zero for equal/reversed
times or a counter reset. Ratios safely return zero when their denominator is
zero. Outlier ratios may be capped before later normalization.

This is an experimental ranking heuristic, not a virality prediction.

## Rights classification

Every discovered source defaults to `unknown`:

- `unknown`: rights have not been established.
- `idea_only`: use only as research/inspiration, not as reusable media.
- `licensed`: a suitable license has been verified.
- `owned`: the operator owns the source.

Public availability, metadata access, or a high trend score never changes the
rights state automatically.

## Free-first stack

The intended MVP uses the existing Python/FastAPI application, existing SQLite
storage where appropriate, official YouTube APIs, Ollama/local open-source LLMs,
existing Whisper/STT, FFmpeg, and later local embeddings only if justified. CP0
adds no package and no paid/cloud AI dependency.

## Tests

Baseline, before CP0:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

Result: collection stopped with 2 errors, 9 warnings, and no completed-suite
pass/fail/skip counts. Missing license-server modules broke
`tests/test_license_telemetry.py`; a missing `TranscriptSegment` re-export broke
`tests/test_voice_karaoke_no_truncation.py`.

The remaining-suite command was also attempted with those two files ignored;
it emitted 37 passing progress markers and then produced no further output for
several minutes, so it was interrupted without a summary.

Focused baseline characterization:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src tests
node --check src/universal_video_ai/web/static/app.js
node --check src/universal_video_ai/web/static/i18n.js
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_youtube_research.py tests/test_youtube_research_api.py tests/test_youtube_research_database.py tests/test_web_static_ui.py tests/test_web_store.py tests/content_os/test_foundation.py
```

Result: 54 passed, 4 failed, 1 skipped, 8 warnings. The failures were three
stale static-UI expectations and one Content OS terminal-state expectation.

CP0 focused test command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_channel_agent.py
```

Initial CP0 result: 16 passed, 0 failed, 0 skipped.

Post-change regression commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q

PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src tests
node --check src/universal_video_ai/web/static/app.js
node --check src/universal_video_ai/web/static/i18n.js
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_channel_agent.py tests/test_youtube_research.py tests/test_youtube_research_api.py tests/test_youtube_research_database.py tests/test_web_static_ui.py tests/test_web_store.py tests/content_os/test_foundation.py

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_channel_agent.py tests/test_youtube_research.py tests/test_youtube_research_api.py tests/test_youtube_research_database.py
```

Results:

- Full suite: the same 2 collection errors and 9 warnings as baseline.
- Syntax checks: passed.
- Focused comparison suite: 70 passed, the same 4 failed, 1 skipped, and 8
  warnings as baseline. The increase from 54 to 70 passes is the 16 CP0 tests.
- CP0 plus adjacent YouTube research: 28 passed, 0 failed, 0 skipped.

Credential-free import checks were run with YouTube, Ollama, OpenAI, Gemini,
and Content OS API-key environment values explicitly blank for both flag states.
Both imports/bootstrap checks passed; the existing Redis client reported its
normal in-memory fallback.

The Uvicorn launcher was also smoke-tested on local port 8766 with the flag
false and true. `GET /api/channel-agent/status` returned HTTP success with the
expected disabled and enabled JSON payloads; both temporary server processes
were stopped after the checks.

## Manual verification

Shell environment values override `.env` because dotenv loads with
`override=False`. The actual launcher defaults to port 8080.

Disabled:

```bash
AI_CHANNEL_AGENT_ENABLED=false python scripts/run_web.py
curl http://127.0.0.1:8080/api/channel-agent/status
```

Expected: the existing app starts normally, the endpoint reports `enabled` as
false, and the Channel Agent tab remains hidden after login.

Enabled:

```bash
AI_CHANNEL_AGENT_ENABLED=true python scripts/run_web.py
curl http://127.0.0.1:8080/api/channel-agent/status
```

Expected: the app starts without YouTube/Ollama credentials, the endpoint
reports `enabled` as true, and the tab appears after login with real CP0 states
and zero persisted CP0 items.

## CP0 known limitations (historical baseline)

- CP0 had no Channel Agent YouTube connection or Analytics API client.
- Ollama is deliberately not probed in CP0, so its status is unknown.
- Placeholder counts are zero because CP0 has no collectors or persistence.
- The pre-existing full test suite has two collection errors, and the focused
  baseline has four unrelated failures described above.
- CP0 does not mount or expand the existing YouTube research router.

## CP1 — YouTube Read-Only Connection

CP1 reuses the established per-user social connection rather than creating a
second Google OAuth system. The browser starts at
`GET /api/social/connect/youtube`, returns to
`/api/social/callback/youtube`, and stores the result in the existing
`social_accounts` row for the authenticated application user. The callback's
single-use `oauth_states` record binds the returned credential to the user who
started the flow.

The existing private-upload permission remains in the requested scope set so a
reconnect does not remove upload capability from the old publishing workflow.
CP1 adds only:

```text
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/yt-analytics.readonly
```

No monetary/revenue scope is requested. Newly granted scopes are recorded on
the existing credential row. A legacy credential without recorded Analytics
permission is not silently assumed to have it: the dashboard asks the user to
reconnect and Google is called with `prompt=consent`. A refresh token does not
gain new scopes by itself.

### Google Cloud setup

In the same Google Cloud project and OAuth client already used by the web app:

1. Enable **YouTube Data API v3**.
2. Enable **YouTube Analytics API**.
3. Configure the OAuth consent screen and add the YouTube/Analytics scopes
   above (plus the existing upload scope used by legacy publishing).
4. Use an OAuth client of type **Web application**.
5. Add the exact authorized redirect URI used by this installation, for
   example `http://127.0.0.1:8080/api/social/callback/youtube` for local use.
6. Set the existing environment variables without committing their values:

   ```env
   GOOGLE_CLIENT_ID=your-oauth-client-id
   GOOGLE_CLIENT_SECRET=your-oauth-client-secret
   ```

An OAuth Client ID/Client Secret identifies the application and drives user
consent. A YouTube API key is a different credential; CP1 does not require one
because every request reads the authenticated user's own channel with OAuth.

If the consent screen is in Testing mode, add intended accounts as test users.
Never put an access token or refresh token in source, documentation, browser
JavaScript, or a Channel Agent API response.

### CP1 service/API behavior

The service obtains the current user's `social_accounts` row, verifies scope
evidence, and refreshes an expired access token centrally through the existing
`GoogleOAuth` client. It then makes direct `requests` calls to official Google
REST endpoints. No `google-api-python-client` or other dependency was added.

Authenticated, feature-gated endpoints are:

```text
GET /api/channel-agent/status
GET /api/channel-agent/youtube/status
GET /api/channel-agent/youtube/channel
GET /api/channel-agent/youtube/overview?days=28
GET /api/channel-agent/youtube/top-videos?days=28&limit=10
GET /api/channel-agent/youtube/traffic-sources?days=28
GET /api/channel-agent/youtube/content-type?days=28
```

Reports also accept `start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`. The default
28-day range ends yesterday. Top-video metadata is enriched with one batched
`videos.list` request, not one request per video. Content type uses the
`creatorContentType` Analytics dimension and returns `available: false` when
that report is unsupported for the connected channel/account context.

The dashboard loads only when its tab is opened or Refresh is clicked. It does
not poll Google. A channel with zero subscribers, views, videos, or report rows
is a valid connected channel and displays “No analytics data yet.”

### CP1 manual smoke test

PowerShell:

```powershell
$env:AI_CHANNEL_AGENT_ENABLED='false'; python scripts/run_web.py
$env:AI_CHANNEL_AGENT_ENABLED='true'; python scripts/run_web.py
```

POSIX shell:

```bash
AI_CHANNEL_AGENT_ENABLED=false python scripts/run_web.py
AI_CHANNEL_AGENT_ENABLED=true python scripts/run_web.py
```

Open `http://127.0.0.1:8080`, log in, open AI Channel Agent, and connect or
reconnect YouTube. Complete Google consent and verify channel identity,
lifetime statistics, 28-day overview, top videos, traffic sources, and content
type. Empty tables must remain a successful zero-data state.

These APIs use the existing `vai_session` HttpOnly cookie. A browser-exported
cookie can be supplied for local diagnostics without printing it:

```bash
curl -b cookies.txt http://127.0.0.1:8080/api/channel-agent/youtube/status
curl -b cookies.txt "http://127.0.0.1:8080/api/channel-agent/youtube/overview?days=28"
```

Do not paste session cookies into issue reports or commit `cookies.txt`.

## Next checkpoint after CP1

**CP2 — YouTube Trend Scanner**
