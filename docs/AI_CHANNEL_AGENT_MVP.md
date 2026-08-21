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

## CP2 — YouTube Trend Scanner

CP2 is a manual, metadata-only research tool. It uses the authenticated
YouTube Data API and never invokes yt-dlp, downloads a source video/audio,
fetches subtitle media, or stores thumbnail binaries. Thumbnail URLs remain
metadata links.

The scan flow is:

```text
enabled user queries
  → search.list
  → batched videos.list and channels.list
  → canonical per-user candidates
  → append-only metric snapshots
  → deterministic query/topic relevance gate
  → capped recent-channel baseline enrichment
  → deterministic opportunity ranking
```

The existing web `trend_scans` and canonical `trend_items` storage is reused.
CP2 adds per-user query rows, candidate/query matches, append-only snapshots,
and scoring fields. A video matching several queries remains one candidate.

### Signals and scoring

- **Observed VPH** uses two repeated snapshots and the CP0 `view_velocity`
  primitive. Equal timestamps, negative deltas, and counter resets safely
  produce zero. It is unavailable on the first scan.
- **Approximate VPH** is explicitly separate and uses current views divided by
  age, with a one-hour minimum denominator. It is a low-confidence first-scan
  estimate, never presented as observed velocity.
- **Engagement** reuses CP0 `(likes + comments) / views` handling.
- **Outlier ratio** compares the candidate with the median views of up to ten
  recent videos from that channel. Long-form scans exclude baseline videos
  below twenty minutes. Missing/zero baselines leave the signal unavailable.
- **Freshness** uses continuous seven-day exponential decay bounded to 0–1.
- **Competition proxy** is an inverse supply heuristic using result and unique
  channel counts. It is not keyword search volume or true competition.
- **Trend score** keeps the CP0 30/25/20/15/10 signal weights and renormalizes
  only the available weights, so a missing outlier does not become a hidden
  penalty. The result is a ranking heuristic, not a viral probability.
- **Confidence** is high only with all five signals and observed velocity;
  three or more available signals are medium, otherwise low.

Threshold labels are `HOT` at 90+, `RISING` at 75+, `WATCH` at 60+, and
`NORMAL` below 60. These are documented display categories, not guarantees.
Explanations are deterministic templates; CP2 uses no LLM.

## CP2.1 — Trend Relevance & Quality Gate

CP2.1 keeps `trend_score` unchanged and adds a separate deterministic
`niche_relevance_score`. Relevance is computed locally from each saved search
query, optional per-query topic terms, optional per-query exclusion terms, and
the result title/description metadata already returned by `videos.list`.
There is no LLM, embedding model, paid API, or media download.

Text is normalized with Unicode NFKC, case folding, accent removal for Latin
text, punctuation folding, Latin word tokens, and overlapping two-character
Chinese motifs. This makes Chinese, Vietnamese, and English profiles usable
without a language-specific tokenizer.

The bounded relevance heuristic uses:

- exact query in title: `+0.65`; exact query only in description: `+0.20`
- configured topic terms in title: `+0.18` each, capped at `0.45`
- configured topic terms only in description: `+0.06` each, capped at `0.18`
- query/topic motif matches in title: `+0.18` each, capped at `0.54`
- motif matches only in description: `+0.05` each, capped at `0.15`
- configured exclusions in title: `-0.50` each; description: `-0.15` each,
  with total exclusion penalty capped at `0.80`

Title evidence intentionally outweighs description evidence. Exclusion terms
are profile configuration, not a permanent global blacklist. The UI exposes
comma-separated topic and exclusion fields on each saved query. Optional
installation-wide additions are also supported:

```env
CHANNEL_AGENT_TREND_TOPIC_TERMS=家族,修仙,长生,老祖,宗门,凡人修仙,多子多福
CHANNEL_AGENT_TREND_EXCLUSION_TERMS=短剧,电视剧
CHANNEL_AGENT_TREND_MIN_RELEVANCE=0.55
```

These values are examples for the current cultivation experiment, not
hardcoded keywords. Other channels should provide their own topic profile.

The final ranking is:

```text
opportunity_score = trend_score * niche_relevance_score
```

Both inputs and the result are bounded `0..1`. A high-velocity but unrelated
video therefore cannot outrank a strongly relevant candidate solely through
raw momentum. Candidates below the configured relevance threshold are still
stored, snapshotted, and assigned their independent trend score. They are
marked `low_relevance` and omitted from the default Ranked Research
Opportunities response/table. The **Show low relevance** control and
`include_filtered=true` API query expose them for audit.

The candidate endpoint accepts `min_relevance` and `include_filtered`.
Historical candidates are migrated additively as `unscored`; no snapshot is
deleted. Rescanning recalculates relevance and opportunity from current
metadata and the saved query profile.

### Research seed queries

These are editable starting ideas, not guaranteed winning keywords:

```text
家族修仙 一口气看完
修仙家族 超长合集
长生家族
长生世家
家族老祖
长生老祖
建立修仙家族
凡人家族修仙
弱小家族崛起
苟道长生 家族
```

No seed is inserted automatically. Users create, edit, enable, or delete their
own queries in the dashboard.

### Rights

External discoveries default to `idea_only`: they are idea/reference sources
until usage rights are independently verified. A result matching the connected
user's own channel may be marked `owned`. Public metadata, a high score, or a
YouTube link never establishes a license or permission to reupload.

### API quota discipline

Scanning occurs only when the user clicks **Scan Now**. Defaults are capped at
five enabled queries, ten search results per query, and five channels selected
for baseline enrichment. Environment guards are:

```env
CHANNEL_AGENT_TREND_MAX_QUERIES=5
CHANNEL_AGENT_TREND_RESULTS_PER_QUERY=10
CHANNEL_AGENT_TREND_MAX_ENRICHMENT_CHANNELS=5
CHANNEL_AGENT_TREND_MIN_RELEVANCE=0.55
```

Hard maximums are 10 queries, 25 results/query, and 10 enrichment channels.
Search has no automatic pagination, daemon, scheduler, or polling loop.

### CP2 manual verification

```bash
AI_CHANNEL_AGENT_ENABLED=true python scripts/run_web.py
```

Log in, open AI Channel Agent, add an editable query, and click Scan Now. The
first scan should create one snapshot per unique candidate, leave observed VPH
unavailable, and optionally show approximate VPH. After waiting, scan again;
repeated candidates should have at least two snapshot rows and observed VPH.

## CP3 — Competitor Intelligence

CP3 is a manual, metadata-only research layer built on qualified CP2
candidates. It answers which channels repeatedly produce relevant breakout
videos without downloading video, audio, subtitles, or thumbnail binaries.
It uses the existing per-user Google OAuth token service and official YouTube
Data API REST calls.

The workflow is:

```text
qualified CP2 candidates
  → deduplicated YouTube channel IDs
  → batched channels.list metadata
  → uploads playlist + playlistItems.list
  → batched videos.list metadata
  → long/short/all comparable sample
  → baseline, breakout, patterns, duration analysis
  → append-only competitor snapshot
```

Users may also add a channel ID, handle, or channel URL manually. Manual
resolution uses official `channels.list` selectors where possible and at most
one `search.list` channel lookup as a fallback. Competitors are canonical per
`user_id + platform + channel_id`; a channel discovered by several candidates
is stored once.

### Baseline, breakouts, and score

The default sample is the 20 most recent uploads, capped at 50. Long-form mode
compares videos of at least 20 minutes; short mode compares videos of at most
3 minutes; all mode is available for mixed research. The baseline is median
views of the comparable recent sample. Missing or zero baselines leave outlier
ratios unavailable rather than inventing a value.

Breakout labels are deterministic research labels:

```text
2x or more   above baseline
5x or more   strong
10x or more  exceptional
```

They are not viral guarantees. Breakout frequency is the proportion of the
analyzed sample at or above 2x its median. Consistency is the proportion with
at least half the median. Upload cadence is a recent-sample estimate based on
observed publish timestamps. Fewer than five analyzed videos is low
confidence, 5–14 is medium, and 15 or more is high.

The bounded competitor ranking heuristic is:

```text
30% breakout frequency
25% median opportunity score of matching CP2 candidates
20% median niche relevance of matching CP2 candidates
15% log-normalized recent median views
10% consistency
```

Available weights are renormalized when a signal is missing. Subscriber count
is deliberately not a score component, so a smaller channel with repeated
outliers can outrank a large but weak channel. The score is experimental and
is not a success prediction.

### Patterns, durations, and opportunity gaps

Title patterns use local Unicode normalization, Latin tokens, overlapping
Chinese bigrams, configured per-query topic terms, and repeated observed term
pairs. A term or pair must occur in at least two sampled titles. Every pattern
retains actual video IDs, titles, and YouTube URLs as evidence; the service
does not fabricate semantic clusters and uses no LLM or embeddings.

Duration analysis reports count, median views, median outlier, and breakout
count for under 20, 20–40, 40–60, 60–90, 90–120, and 120+ minute buckets.
These are descriptive sample ranges, not causal claims.

The opportunity-gap foundation aggregates patterns that have actual breakout
evidence across competitors. It reports supporting competitor and breakout
counts, median outlier, the number of current qualified candidates matching
the pattern, an inverse-supply competition proxy, confidence, and inspectable
evidence links. It does not generate topics, scripts, titles, or production
jobs.

### Persistence, isolation, rights, and quota

Competitor channels, recent-video metadata, and append-only channel snapshots
use additive SQLite tables and idempotent migrations. All reads and mutations
are scoped to the authenticated application user. Access tokens and refresh
tokens remain only in the existing OAuth storage.

Competitor video rows are `idea_only` research/reference evidence. CP3 has no
download, reupload, publishing, or rights claim action.

Discovery and refresh are explicit buttons; there is no scheduler or polling.
Quota controls are:

```env
CHANNEL_AGENT_COMPETITOR_MAX_CHANNELS=10
CHANNEL_AGENT_COMPETITOR_RECENT_VIDEOS=20
```

The first value is hard-capped at 20 and the second at 50. Refresh uses one
uploads-playlist request per selected competitor and combines video IDs into
`videos.list` batches of up to 50. It does not use `search.list` for routine
recent-video collection.

### CP3 manual verification

```bash
AI_CHANNEL_AGENT_ENABLED=true python scripts/run_web.py
```

Log in, open AI Channel Agent, ensure qualified Trend Scanner candidates
exist, click **Discover from Trends**, then **Refresh**. Verify channel cards,
median baseline, breakout videos, evidence-backed title patterns, duration
buckets, and opportunity gaps. Open evidence links to confirm their source
videos. No media should be downloaded.

## CP3.1 — Competitor Relevance and Pattern Quality Gate

CP3.1 preserves the independent CP3 `competitor_score` and raw evidence while
adding a separate channel relevance decision. The profile is assembled from
enabled saved query text, per-query topic/exclusion terms, and optional
environment additions. Strong terms are derived from that user profile;
cultivation vocabulary is not permanently built into the scoring engine.

For each recent video, title matches carry more weight than description
matches. Generic/support terms cannot create a niche hit on their own.
Channel relevance combines available signals as follows:

```text
45% matching recent-video rate
25% median recent-video relevance
20% median qualified CP2 candidate relevance
10% channel-name relevance
```

Channel-name exclusions and the proportion of excluded recent videos apply
explicit penalties. A channel needs both a score of at least 0.55 and a recent
niche-hit rate of at least 30% to be `qualified`. Scores of at least 0.35 with
at least a 15% hit rate are `watch`; weaker channels are `low_relevance`.
Channels without a usable recent sample/profile remain `unscored`. These
statuses do not alter `competitor_score`.

Pattern extraction still retains observed single terms and compounds, but now
stores bounded relevance, specificity, distinct-video support, quality score,
and status. Quality combines 45% niche association, 25% specificity, 15%
distinct-video support, and 15% breakout support. Generic single terms such
as format markers can remain visible in audit mode but cannot qualify by
frequency alone. Duplicate video IDs count once.

Default Opportunity Gaps require qualified pattern evidence from qualified
competitors, breakout evidence, and two distinct competitor channels. Gap
quality combines pattern quality, cross-channel support, and unique breakout
support. **Show filtered patterns** exposes rejected/watch evidence without
deleting it.

The small default generic and exclusion profiles are replaceable. Relevant
controls are:

```env
CHANNEL_AGENT_COMPETITOR_STRONG_TERMS=
CHANNEL_AGENT_COMPETITOR_GENERIC_TERMS=家族,前世,穿越,一口气看完,合集,完结,完整版,一个,时候
CHANNEL_AGENT_COMPETITOR_EXCLUSION_TERMS=短剧,短劇,电视剧,電視劇,甜宠,甜寵,霸总,霸總,都市剧,都市劇
CHANNEL_AGENT_COMPETITOR_MIN_RELEVANCE=0.55
CHANNEL_AGENT_COMPETITOR_WATCH_RELEVANCE=0.35
CHANNEL_AGENT_PATTERN_MIN_SUPPORT=2
CHANNEL_AGENT_PATTERN_MIN_QUALITY=0.55
CHANNEL_AGENT_GAP_MIN_COMPETITORS=2
```

Existing competitor rows migrate additively to `unscored` and become scored
on the next explicit Refresh. **Show low relevance** retrieves filtered rows
for audit. No competitor, video, pattern evidence, or snapshot is deleted.

## CP4 — Local Ollama Content Brain

CP4 is an interpretation layer over the existing CP1–CP3 evidence. It does not
collect a second copy of research data, download source media, read source
transcripts, create full production scripts, or enqueue production. The only
AI provider implemented for this checkpoint is a locally running Ollama HTTP
server. No paid-provider SDK or cloud fallback is installed.

The backend resolves an authenticated selector (`top_opportunity`, `candidate`,
`competitor`, or qualified gap) against SQLite. Browser-submitted metrics and
arbitrary evidence JSON are rejected. By default the deterministic evidence
assembler includes only:

- up to five qualified CP2 candidates ranked by opportunity score;
- up to five CP3 competitors that passed the competitor relevance gate;
- qualified patterns and qualified opportunity gaps;
- canonical, deduplicated breakout video metadata selected across different
  qualified channels;
- optional CP1 own-channel identity and 28-day overview, when connected; and
- application-computed evidence confidence plus missing-signal explanations.

Descriptions, transcripts, subtitles, scripts, media bytes, OAuth tokens, and
local file paths are excluded. Item count and serialized evidence size are
bounded before prompting. The normalized JSON is hashed with deterministic
key ordering and persisted with the request type and configured model.

The system prompt, quoted evidence block, request mode, and JSON schema are
separate. YouTube titles, channel names, and all metadata are explicitly marked
as untrusted data; instructions embedded in them must not be followed. Model
JSON is validated by the application. Markdown fences and harmless text outside
the first complete JSON object are removed, unknown extra fields are ignored,
optional arrays receive empty defaults, and confidence casing is normalized.
Unknown evidence IDs, unsupported factual claims, and viral guarantees are
rejected rather than repaired. Quantitative evidence displayed in the dashboard is
read from the stored evidence bundle, not copied from model prose.

The four explicit modes are `opportunity_analysis`, `content_angles`,
`title_hooks`, and `longform_outline`. Each mode receives a strict, smaller
mode-specific schema: analysis does not generate angles/titles/outline, angles
does not generate titles/hooks/outline, title/hooks does not generate an
outline, and outline does not generate the other creative sections. This keeps
local generation bounded while retaining the same evidence-ID validation.
Results are Vietnamese and include competitive differentiation, risks,
evidence references, and an AI interpretation confidence when that mode needs
them. Content Angles prefers three distinct items while accepting two supported
items; Titles & Hooks accepts 2-8 titles and 3-9 individual hooks. The dashboard separately
treats deterministic evidence confidence as authoritative. When evidence
confidence is low, the default response is a non-AI insufficient-evidence
summary; the user must explicitly opt into low-confidence brainstorming to
invoke Ollama.

The active result panel is keyed by the returned run ID and request type. A new
Analyze click immediately replaces the prior result with a mode-specific loading
panel; failures replace it with a mode-specific error instead of leaving stale
content visible. A monotonically increasing browser request token prevents an
older response from overwriting a newer mode selection. History inspection uses
the stored request type and result through a GET request and never invokes Ollama.

Structured requests send `think: false` to supported Ollama versions so Qwen3
and similar models do not spend the local generation budget on hidden reasoning.
An older server that explicitly rejects the field is retried once without that
field; `format: "json"`, output bounds, and application validation remain in
force. Logs contain only model, mode, evidence-item count, prompt length,
generation settings, retry number, response character count, classified failure
stage, outcome, and elapsed time—not prompt or response contents or secrets.

Each mode prompt contains one compact required JSON shape, one compact valid
example, and the explicit allowed evidence-ID list. If parsing or a repairable
schema/count/runtime check fails, Content Brain makes exactly one JSON-only
repair request at low temperature. The repair request contains the validation
error metadata and prior response as untrusted quoted data. Network failures,
timeouts, unknown evidence IDs, policy failures, authorization failures, and
selector failures are never automatically retried. Runtime allocations totaling
95-105 are normalized to exactly 100; totals outside that tolerance fail or use
the single bounded repair attempt.

Content Brain runs are stored in the additive `content_brain_runs` table with
per-user ownership, status, provider/model, context label, evidence hash,
normalized evidence, validated result, timestamps, sanitized error,
`generation_attempt_count`, and `failure_stage`.
History inspection never reruns AI. A process-local per-user guard permits only
one generation at a time.

### Local configuration

```env
AI_CHANNEL_AGENT_ENABLED=true
CHANNEL_AGENT_OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=
CHANNEL_AGENT_BRAIN_TIMEOUT_SECONDS=120
CHANNEL_AGENT_BRAIN_MAX_EVIDENCE_ITEMS=18
CHANNEL_AGENT_BRAIN_MAX_PROMPT_CHARS=30000
CHANNEL_AGENT_BRAIN_TEMPERATURE_ANALYSIS=0.15
CHANNEL_AGENT_BRAIN_TEMPERATURE_CREATIVE=0.35
CHANNEL_AGENT_BRAIN_REPAIR_TEMPERATURE=0.0
CHANNEL_AGENT_BRAIN_TOP_P=0.85
CHANNEL_AGENT_BRAIN_NUM_PREDICT_OPPORTUNITY_ANALYSIS=900
CHANNEL_AGENT_BRAIN_NUM_PREDICT_CONTENT_ANGLES=1100
CHANNEL_AGENT_BRAIN_NUM_PREDICT_TITLE_HOOKS=900
CHANNEL_AGENT_BRAIN_NUM_PREDICT_LONGFORM_OUTLINE=1600
```

`OLLAMA_MODEL` has no CP4 default. Select a model that is already installed
locally. The application never runs `ollama pull` and never guesses a model.
Any compatible instruction-capable multilingual Ollama model may be used; a
good choice should understand Chinese evidence, produce Vietnamese, and follow
a structured JSON schema. Model names in Ollama documentation or community
examples are examples, not mandatory dependencies.

### Windows manual setup

1. Install Ollama from its official Windows distribution.
2. Start Ollama using the normal Ollama application/service for that install.
3. In PowerShell, manually install or confirm the local model you chose using
   Ollama's own CLI. CP4 does not do this for you.
4. Put the exact installed model name in `OLLAMA_MODEL` in `.env`; keep
   `OLLAMA_BASE_URL=http://127.0.0.1:11434` unless you intentionally changed it.
5. Start the app:

   ```powershell
   $env:AI_CHANNEL_AGENT_ENABLED='true'
   .\venv\Scripts\python.exe .\scripts\run_web.py
   ```

6. Log in and open **AI Channel Agent → Content Brain**.
7. Click **Refresh status** and confirm Ollama is reachable and the configured
   model is available.
8. Select qualified stored evidence and run **Opportunity Analysis** once.

### Ubuntu manual setup

1. Install Ollama from its official Ubuntu/Linux distribution.
2. Start Ollama using the normal command or service documented for that install.
3. Manually install or confirm the local model you chose with the Ollama CLI.
4. Set the exact model name and local endpoint in `.env` as shown above.
5. Start the app:

   ```bash
   AI_CHANNEL_AGENT_ENABLED=true python scripts/run_web.py
   ```

6. Log in, open **AI Channel Agent → Content Brain**, refresh status, and run
   one analysis against a real qualified opportunity.

### CP4 live verification

With Ollama running and an installed model selected, open Content Brain and
confirm **Ollama connected**, the exact configured model, and **Ready**. Select
a real qualified candidate or gap and run **Opportunity Analysis**. Verify that:

- the evidence panel shows stored trend, relevance, opportunity, competitor,
  pattern, gap, and breakout values;
- the returned recommendation is Vietnamese and every displayed evidence
  reference resolves to the supplied bundle;
- video evidence links open the original YouTube page without embedding or
  downloading it;
- no invented metric appears and no tracebacks reach the browser; and
- a new history row opens the same persisted result without another generation.

Then run **Content Angles**, **Titles & Hooks**, and **Long-form Outline**. For
the current cultivation research profile, confirm the Vietnamese ideas use
observed motifs such as family cultivation or longevity only when those motifs
actually exist in the selected evidence. Confirm the output proposes a new
Vietnamese perspective and structure instead of translating or reproducing a
competitor title or plot.

For the CP4.2 reliability smoke test, enable low-confidence brainstorming and
run each of the four modes three times with the selected local model (12 runs).
Target at least 11 completed runs. Inspect any failed history row for its
sanitized failure stage and attempt count; confirm no stale result remains and
every displayed evidence ID exists in the evidence panel.

Also verify failure states by stopping Ollama, clearing `OLLAMA_MODEL`, and
configuring a non-installed model in turn. The UI should report each condition
without affecting Trend Scanner, Competitor Intelligence, or localization.

## CP5 — Content Opportunity Engine

CP5 converts stored CP2 candidates, CP3 opportunity gaps, and completed CP4
runs into per-user decision cards. It is deterministic and remains available
when Ollama is stopped. Creating, refreshing, approving, or archiving a card
does not call Ollama, download media, create a localization/production job, or
publish anything.

Cards have explicit `draft`, `watch`, `approved`, `rejected`, and `archived`
states. Supported transitions are draft → watch/approved/rejected, watch →
draft/approved/rejected, approved → archived, and rejected → draft. Approval
means eligible for later production planning only. Each create, refresh, CP4
link, and status change is recorded in a small per-user event history.

### Deterministic scoring and ranking

The evidence score uses these maximum component weights:

- trend strength: 20;
- niche relevance: 20;
- candidate opportunity strength: 15;
- qualified competitor and breakout evidence: 15;
- qualified pattern or gap quality: 15; and
- application-side evidence confidence: 15.

Only stored, available signals participate. Missing signals are marked unknown
and available weights are normalized to 100; they are never silently converted
to fabricated zero measurements. The score explanation preserves each signal,
weight, raw points, normalized points, and total. Model self-ratings never
enter this score.

Evidence confidence remains a separate low/medium/high value. It considers
observed versus approximate VPH, candidate count, qualified competitor count,
distinct breakout evidence, cross-channel support, usable own-channel history,
and CP4's application-computed evidence confidence. A sparse idea can therefore
show a high evidence score and low confidence, with explicit “Waiting for”
signals.

Competition is unknown when the observed sample is too small. Otherwise it is
low (shown as “limited observed”), medium, or high based on qualified
competitor count, qualified candidate supply, breakout support, and
cross-channel support. Rank is:

`evidence score × confidence factor × freshness factor × competition adjustment`

Confidence factors are 0.72/0.88/1.0 for low/medium/high. Competition factors
are 1.05/1.0/0.95/0.95 for low/medium/high/unknown. This makes confidence
material while applying only a small penalty to competitive topics that also
demonstrate demand. Freshness factors are 1.0/0.92/0.82 for fresh/aging/stale
evidence. Scores and ranks are bounded to 0–100.

### Snapshots, CP4 enrichment, and rights

Each card stores a normalized `cp5-v1` evidence snapshot and deterministic
SHA-256 hash. It contains bounded candidate, competitor, pattern, gap, breakout,
own-channel, confidence, and rights metadata already resolved under the logged-
in user. It excludes OAuth credentials, secrets, raw provider payloads,
transcripts, and media. Duplicate candidate/gap/evidence keys return the
existing card.

When matching completed CP4 runs exist, CP5 reuses stored angle, audience
promise, conflict, differentiation, title, hook, and risks. It never triggers a
new analysis. Manual evidence refresh recalculates system research fields while
preserving working title, selected angle, notes, priority, format, and duration.

External sources remain `idea_only` or `unknown` unless separately verified.
Approving the idea never changes source rights. Production should extract the
motif/problem, create an original Vietnamese script/commentary and sequence,
and use owned, licensed, or permitted visuals.

### CP5 manual verification

1. Start the app with `AI_CHANNEL_AGENT_ENABLED=true` and log in.
2. Open **AI Channel Agent → Trend Scanner** and ensure qualified stored
   candidates exist; refresh competitor research if opportunity-gap evidence is
   needed.
3. Open **Content Opportunities** and click **Generate from top research**.
   Confirm no more than five deduplicated cards are produced by the default
   action.
4. Open a card and inspect evidence score, component explanation, confidence,
   competition, freshness, evidence links, rights, and optional CP4 enrichment.
5. Save a working title, selected angle, notes, priority, and target duration;
   then click **Refresh evidence** and confirm those editorial choices remain.
6. Exercise draft → watch → approved → archived, and on another card draft →
   rejected. Confirm each event is visible.
7. Stop Ollama and reload the board. Listing, filtering, creating, refreshing,
   and changing card status must continue to work.
8. Confirm no production/localization job, source download, render, upload, or
   publish action was created.

## CP6 — Production Queue

CP6 creates one per-user production planning item from an explicitly approved
CP5 opportunity. The database uniqueness rule on `(user_id, opportunity_id)`
makes repeated clicks and competing requests resolve to the same queue item.
Draft, watch, rejected, and archived opportunities are rejected server-side.
No arbitrary queue item can be created without that traceable approved source.

The deterministic `cp6-v1` brief copies the current CP5 editorial choices and
reuses CP4-derived angle, audience promise, conflict, differentiation, title,
hook, and risk fields already stored on the opportunity. If CP4 enrichment is
missing, an evidence-only brief is still created. CP6 never calls Ollama. The
brief retains a bounded list of evidence references and the source evidence
hash, but excludes prompts, provider payloads, tokens, transcripts, and media.

Every item receives six planning tasks: SCRIPT; VISUAL_PLAN, VOICE_PLAN,
THUMBNAIL, and METADATA after SCRIPT; then QA after those planning tasks. Task
readiness is derived from persisted dependencies. Required-task progress is
`completed required tasks / total required tasks`; optional skipped tasks do
not create fake progress. Task actions record manual start, completion, block,
skip (optional only), and notes. They do not execute script generation, TTS,
image generation, rendering, upload, or publishing.

Item states are `queued`, `planning`, `ready`, `in_progress`, `blocked`,
`completed`, and `cancelled`, with explicit server-side transitions. Item and
task changes create a bounded audit timeline. User priority remains separate
from the inherited CP5 opportunity rank. Manual **Sync from Opportunity**
updates the brief, title, angle, target, rank, and source-rights context while
preserving production notes, priority, blocker, and all task states.

Planning readiness and rights readiness are deliberately separate. External
evidence keeps its `idea_only`/`unknown` source status; the initial gate is
`research_only` or `needs_review`. Approval of an idea never clears media
rights. Production guidance continues to require an original Vietnamese
script/commentary and sequence using owned, licensed, or permitted visuals.

### CP6 manual verification

1. Start the app with `AI_CHANNEL_AGENT_ENABLED=true`, log in, and open
   **AI Channel Agent → Content Opportunities**.
2. Open an approved opportunity and click **Create Production Item**. Repeat
   the action and confirm the existing item is returned rather than duplicated.
3. Open **Production Queue** and verify the queued item, deterministic brief,
   separate planning/rights readiness, six tasks, dependencies, zero progress,
   and event history.
4. Move queued → planning. Start and complete SCRIPT; confirm VISUAL_PLAN,
   VOICE_PLAN, THUMBNAIL, and METADATA become ready. Complete them and confirm
   QA becomes ready. Complete QA and confirm the planning item completes.
5. On a second approved item, block a required task and confirm the item is
   visibly blocked. Add task/production notes, edit the CP5 title or angle,
   then use **Sync from Opportunity** and verify task states, notes, and the
   blocker remain unchanged.
6. Confirm the rights gate remains research-only/needs-review for external
   evidence even though planning can proceed.
7. Stop Ollama and repeat listing, creation, sync, task updates, and history.
   Confirm they still work.
8. Verify no source download, legacy localization job, render, upload, or
   publishing action occurs anywhere in the flow.

## CP7A — Script & Asset Production Execution

CP7A executes only the text and structured planning assets attached to an
existing CP6 Production Item. It reuses the configured local Ollama provider
with `think=false`, `format=json`, and `stream=false`; no paid provider is
required. The Production Queue and all saved assets remain readable and
editable when Ollama is stopped.

Long-form scripts are never requested as one giant completion. The workflow is:

`blueprint → independently versioned sections → deterministic assembly → human approval`

The default narration estimate is 145 words/minute and the default blueprint
requests eight sections. Both are configurable. The target total word count is
the target duration multiplied by the configured narration rate, and each
section receives a deterministic allocation based on its blueprint weight.
Word count, estimated duration, target duration, and word-budget tolerance are
displayed as estimates rather than performance guarantees.

```dotenv
CHANNEL_AGENT_SCRIPT_WORDS_PER_MINUTE=145
CHANNEL_AGENT_SCRIPT_SECTION_COUNT=8
CHANNEL_AGENT_PRODUCTION_MAX_PROMPT_CHARS=24000
CHANNEL_AGENT_PRODUCTION_TEMPERATURE=0.25
CHANNEL_AGENT_PRODUCTION_REPAIR_TEMPERATURE=0.0
CHANNEL_AGENT_PRODUCTION_TOP_P=0.85
CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_BLUEPRINT=1200
CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_SECTION=1800
CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_VISUAL=1200
CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_VOICE=700
CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_THUMBNAIL=800
CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_METADATA=1100
```

Each generation uses a minimal asset-specific JSON schema and exactly one
repair retry for repairable JSON/schema failures. Evidence-ID, rights, and
policy failures are not repaired into invented facts. Prompts and model output
are not logged; diagnostics contain only asset type, section ID, model,
prompt/output bounds, attempt number, and elapsed time.

Assets are immutable versions with `draft`, `review`, `approved`, `rejected`,
and `superseded` states. Regeneration creates a new version and never deletes
or overwrites an approved one. SCRIPT and the four downstream CP6 tasks only
complete when their corresponding assembled/package asset is approved. QA is
deterministic and its approval marks the CP7A asset package ready. Asset
readiness and rights readiness remain separate.

Visual plans accept only original generation, owned assets, licensed stock,
public domain, manual creation, diagrams, maps, or text cards. Every scene has
a rights requirement and begins planned/needs-review unless separately
cleared. CP7A does not download sources, invoke TTS, create subtitle/audio
files, render, upload, schedule, or publish.

### CP7A manual verification

1. Configure an already-installed local Ollama model through `OLLAMA_MODEL`,
   start Ollama, and start the app with `AI_CHANNEL_AGENT_ENABLED=true`.
2. Open an existing Production Item and find **Production Execution**.
3. Generate a Script Blueprint and verify multiple bounded sections, target
   word allocations, Vietnamese audience-facing guidance, and research-only
   originality/rights constraints.
4. Generate only the first section, record elapsed time, then generate the
   second. Restart the app and confirm both versions remain visible.
5. Use **Resume / Generate all remaining**. Confirm completed sections are not
   regenerated and progress advances sequentially with concurrency one.
6. Manually edit one section and save it as a new version. Inspect version
   history; confirm the earlier version remains available.
7. Assemble the Script Draft. Submit/approve it and verify SCRIPT completes
   only at approval, unlocking the four dependent CP6 tasks.
8. Generate and approve Visual Plan, Voice Plan, Thumbnail Brief, and Metadata
   Package. Confirm the visual plan contains no source download/reuse strategy.
9. Run deterministic QA and approve its report. Confirm `asset_ready=true`
   while `rights_ready` may remain false.
10. Stop Ollama. Confirm existing assets remain viewable, manually editable,
    versionable, approvable/rejectable, and the queue still works; a new
    generation should fail cleanly without affecting saved work.
11. Confirm no legacy job, source download, TTS/audio/subtitle artifact,
    FFmpeg render, upload, or publishing action was created.

## Next checkpoint

**CP7B — Render Pipeline Integration**
