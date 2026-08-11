# src/universal_video_ai/web/store.py
"""
Lightweight SQLite-backed storage for the web UI: user accounts (for login)
and localization jobs (for history/status/preview/download).

Deliberately stdlib-only (sqlite3) rather than an ORM — this app has two
small tables and doesn't need one.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    credits INTEGER NOT NULL DEFAULT 10,
    is_admin INTEGER NOT NULL DEFAULT 0,
    email TEXT,
    phone TEXT,
    oauth_provider TEXT,
    oauth_id TEXT,
    referral_code TEXT,
    referred_by_user_id INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    source_channel_url TEXT,
    source_channel_title TEXT,
    source_channel_id TEXT,
    source_uploader TEXT,
    target_language TEXT NOT NULL,
    source_language TEXT DEFAULT 'auto',
    status TEXT NOT NULL,           -- queued | running | review | done | error
    progress_note TEXT,
    error TEXT,
    title TEXT,
    final_video_path TEXT,
    source_video_path TEXT,
    logo_path TEXT,
    logo_corner TEXT DEFAULT 'bottom_right',
    logo_size_px INTEGER DEFAULT 120,
    branding_config TEXT,
    publishing_config TEXT,
    publishing_pack_path TEXT,
    publish_ready_video_path TEXT,
    publishing_pack_status TEXT DEFAULT 'disabled',
    publishing_pack_error TEXT,
    tts_voice TEXT,
    review_mode INTEGER NOT NULL DEFAULT 0,
    review_state_json TEXT,
    segments_json TEXT,
    source_segments_json TEXT,
    qc_warnings_json TEXT,
    animated_subtitle_config TEXT,
    video_template_config TEXT,
    remix_enabled INTEGER NOT NULL DEFAULT 0,
    remix_platforms_json TEXT,
    remix_goal TEXT DEFAULT 'viral',
    remix_strength TEXT DEFAULT 'balanced',
    subtitle_offset_seconds REAL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    success INTEGER NOT NULL,
    message TEXT,
    remote_url TEXT,
    created_at REAL NOT NULL
);

-- Per-user OAuth connections to social platforms. Unlike the old
-- single-shared-.env-token model, each row here belongs to exactly one
-- app user, so multiple people using this server each connect their own
-- TikTok/Facebook/YouTube account and publish as themselves.
CREATE TABLE IF NOT EXISTS social_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,          -- tiktok | facebook | youtube
    access_token TEXT,
    refresh_token TEXT,
    expires_at REAL,
    account_name TEXT,               -- display name shown in the UI ("Connected as ...")
    account_ref TEXT,                -- platform-specific id (e.g. FB page id, open_id)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, platform)
);

-- Short-lived CSRF state for the OAuth redirect dance (state -> which
-- app user + which platform initiated it). Rows are deleted once consumed
-- by the callback, and stale ones (>1h) are swept on write.
CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Short-lived CSRF state for the IDENTITY "Sign in with ..." flow (login/
-- register, as opposed to oauth_states above which is for per-user social
-- "connect my account to publish" after already being logged in). Doesn't
-- carry a user_id since the whole point is we don't know who this is yet.
CREATE TABLE IF NOT EXISTS identity_oauth_states (
    state TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- Feedback / bug reports sent in from the app's "Góp ý / Báo lỗi" button.
-- user_id is nullable since someone might file feedback before ever
-- logging in (e.g. from the auth screen), though the UI currently only
-- shows the button once logged in.
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT NOT NULL,
    page TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS top_up_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    credits INTEGER NOT NULL,
    amount_vnd INTEGER NOT NULL,
    payment_method TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    platforms_json TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    hashtags_json TEXT NOT NULL DEFAULT '[]',
    scheduled_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS video_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    template TEXT NOT NULL,
    transition TEXT NOT NULL,
    color_effect TEXT NOT NULL,
    audio_filters_json TEXT,
    video_quality TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS user_provider_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,          -- openai | elevenlabs | playht | cartesia | xtts
    api_key TEXT,
    api_secret TEXT,
    default_model TEXT,
    default_voice TEXT,
    extra_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, provider)
);

CREATE TABLE IF NOT EXISTS trend_scans (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    platforms_json TEXT NOT NULL,
    providers_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress_note TEXT,
    warnings_json TEXT,
    error TEXT,
    max_results INTEGER NOT NULL DEFAULT 20,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trend_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT,
    author TEXT,
    thumbnail_url TEXT,
    duration_seconds REAL,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    published_at TEXT,
    trend_score REAL,
    raw_json TEXT,
    download_status TEXT DEFAULT 'found',
    local_path TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, platform, source_url)
);

-- Content OS tables (feature-flagged content creation workflow)
CREATE TABLE IF NOT EXISTS content_os_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    platforms_json TEXT NOT NULL,
    niche TEXT,
    target_audience TEXT,
    target_market TEXT NOT NULL DEFAULT 'Vietnam',
    default_language TEXT NOT NULL DEFAULT 'vi',
    tone TEXT,
    visual_identity_json TEXT,
    default_voice TEXT,
    subtitle_profile_json TEXT,
    content_rules_json TEXT,
    forbidden_topics_json TEXT,
    preferred_formats_json TEXT,
    publishing_notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS content_os_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER,
    channel_name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'ai_video',
    topic TEXT NOT NULL,
    objective TEXT,
    target_platform TEXT NOT NULL,
    target_duration_seconds INTEGER NOT NULL,
    target_language TEXT NOT NULL DEFAULT 'vi',
    content_style TEXT,
    visual_style TEXT,
    voice_id TEXT,
    subtitle_style_id TEXT,
    background_music_enabled INTEGER NOT NULL DEFAULT 0,
    user_instructions TEXT,
    settings_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(channel_id) REFERENCES content_os_channels(id)
);

CREATE TABLE IF NOT EXISTS content_os_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    workflow_version TEXT NOT NULL DEFAULT '1.0',
    status TEXT NOT NULL DEFAULT 'created',
    current_stage TEXT NOT NULL DEFAULT 'created',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    revision_count INTEGER NOT NULL DEFAULT 0,
    warning_json TEXT,
    error_json TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(project_id) REFERENCES content_os_projects(id)
);

CREATE TABLE IF NOT EXISTS content_os_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input_artifact_ids_json TEXT,
    output_artifact_ids_json TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at REAL,
    completed_at REAL,
    error_json TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES content_os_runs(id)
);

CREATE TABLE IF NOT EXISTS content_os_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    schema_version TEXT NOT NULL DEFAULT '1.0',
    path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    metadata_json TEXT,
    created_by_agent TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES content_os_runs(id)
);

CREATE TABLE IF NOT EXISTS content_os_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT,
    author TEXT,
    thumbnail_url TEXT,
    metrics_json TEXT,
    trend_score REAL NOT NULL DEFAULT 0.0,
    selected INTEGER NOT NULL DEFAULT 0,
    download_status TEXT NOT NULL DEFAULT 'not_downloaded',
    local_path TEXT,
    risk_json TEXT,
    raw_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES content_os_runs(id)
);

CREATE TABLE IF NOT EXISTS content_os_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    scores_json TEXT,
    issues_json TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES content_os_runs(id),
    FOREIGN KEY(artifact_id) REFERENCES content_os_artifacts(id)
);

CREATE TABLE IF NOT EXISTS content_os_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    approval_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES content_os_runs(id)
);

CREATE TABLE IF NOT EXISTS content_os_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_key TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    source_run_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(source_run_id) REFERENCES content_os_runs(id)
);


-- Reusable AI Publishing Pack channel profiles. These are strictly scoped to
-- one app user; no user's channel name/SEO rules become global defaults.
CREATE TABLE IF NOT EXISTS publishing_channel_profiles (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_publishing_profiles_user
    ON publishing_channel_profiles(user_id, updated_at);

-- Persistent channel catalog used by deep/continued profile scans.
CREATE TABLE IF NOT EXISTS channel_scan_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    original_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    channel_id TEXT,
    channel_title TEXT,
    cursor TEXT,
    has_more INTEGER,
    complete INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'incomplete',
    stop_reason TEXT,
    scan_source TEXT,
    network_pages INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    total_discovered INTEGER NOT NULL DEFAULT 0,
    scan_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS channel_scan_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state_id INTEGER NOT NULL,
    video_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    metadata_json TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    discovered_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    UNIQUE(state_id, video_id),
    UNIQUE(state_id, source_url),
    FOREIGN KEY(state_id) REFERENCES channel_scan_states(id)
);

CREATE INDEX IF NOT EXISTS idx_channel_scan_states_user_url
    ON channel_scan_states(user_id, canonical_url);
CREATE INDEX IF NOT EXISTS idx_channel_scan_videos_state_position
    ON channel_scan_videos(state_id, position);
CREATE INDEX IF NOT EXISTS idx_channel_scan_videos_source_url
    ON channel_scan_videos(source_url);

-- Content OS indexes
CREATE INDEX IF NOT EXISTS idx_content_os_projects_user_id ON content_os_projects(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_projects_status ON content_os_projects(status);
CREATE INDEX IF NOT EXISTS idx_content_os_projects_created_at ON content_os_projects(created_at);
CREATE INDEX IF NOT EXISTS idx_content_os_runs_project_id ON content_os_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_content_os_runs_user_id ON content_os_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_runs_status ON content_os_runs(status);
CREATE INDEX IF NOT EXISTS idx_content_os_runs_current_stage ON content_os_runs(current_stage);
CREATE INDEX IF NOT EXISTS idx_content_os_steps_run_id ON content_os_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_content_os_steps_status ON content_os_steps(status);
CREATE INDEX IF NOT EXISTS idx_content_os_channels_user_id ON content_os_channels(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_channels_active ON content_os_channels(active);
CREATE INDEX IF NOT EXISTS idx_content_os_memories_user_id ON content_os_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_memories_channel_key ON content_os_memories(channel_key);
CREATE INDEX IF NOT EXISTS idx_content_os_memories_memory_type ON content_os_memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_content_os_artifacts_run_id ON content_os_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_content_os_artifacts_user_id ON content_os_artifacts(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_artifacts_type ON content_os_artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_content_os_sources_run_id ON content_os_sources(run_id);
CREATE INDEX IF NOT EXISTS idx_content_os_sources_user_id ON content_os_sources(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_sources_selected ON content_os_sources(selected);
CREATE INDEX IF NOT EXISTS idx_content_os_reviews_run_id ON content_os_reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_content_os_reviews_artifact_id ON content_os_reviews(artifact_id);
CREATE INDEX IF NOT EXISTS idx_content_os_approvals_run_id ON content_os_approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_content_os_approvals_user_id ON content_os_approvals(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_memories_user_id ON content_os_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_content_os_memories_channel_key ON content_os_memories(channel_key);
CREATE INDEX IF NOT EXISTS idx_content_os_memories_active ON content_os_memories(active);
"""

_MIGRATIONS = [
    ("users", "credits", "ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 10"),
    ("users", "is_admin", "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
    # Alternative sign-up/sign-in identifiers alongside the original
    # `username`. All three of email/phone/username are optional at the SQL
    # level (a user might only have one of them, e.g. a Google-only login
    # has no password/username at all) — application code enforces that
    # every user has AT LEAST ONE way to log in.
    ("users", "email", "ALTER TABLE users ADD COLUMN email TEXT"),
    ("users", "phone", "ALTER TABLE users ADD COLUMN phone TEXT"),
    # Which identity provider created this account via "Sign in with ..."
    # (NULL for a plain username/password account), and that provider's own
    # unique id for the person, so a later login from the same provider
    # finds the same row again instead of creating a duplicate account.
    ("users", "oauth_provider", "ALTER TABLE users ADD COLUMN oauth_provider TEXT"),
    ("users", "oauth_id", "ALTER TABLE users ADD COLUMN oauth_id TEXT"),
    # Referral program: every user gets a shareable code; referred_by_user_id
    # records who invited them (NULL if nobody / signed up before this
    # feature existed). Bonus credits are granted once, at registration
    # time, in the /api/register handler — this table just tracks the link.
    ("users", "referral_code", "ALTER TABLE users ADD COLUMN referral_code TEXT"),
    ("users", "referred_by_user_id", "ALTER TABLE users ADD COLUMN referred_by_user_id INTEGER"),
    # Per-job source language + optional brand-logo overlay settings,
    # added after jobs already existed in the wild.
    ("jobs", "source_language", "ALTER TABLE jobs ADD COLUMN source_language TEXT DEFAULT 'auto'"),
    ("jobs", "source_channel_url", "ALTER TABLE jobs ADD COLUMN source_channel_url TEXT"),
    ("jobs", "source_channel_title", "ALTER TABLE jobs ADD COLUMN source_channel_title TEXT"),
    ("jobs", "source_channel_id", "ALTER TABLE jobs ADD COLUMN source_channel_id TEXT"),
    ("jobs", "source_uploader", "ALTER TABLE jobs ADD COLUMN source_uploader TEXT"),
    ("jobs", "logo_path", "ALTER TABLE jobs ADD COLUMN logo_path TEXT"),
    ("jobs", "logo_corner", "ALTER TABLE jobs ADD COLUMN logo_corner TEXT DEFAULT 'bottom_right'"),
    ("jobs", "logo_size_px", "ALTER TABLE jobs ADD COLUMN logo_size_px INTEGER DEFAULT 120"),
    ("jobs", "branding_config", "ALTER TABLE jobs ADD COLUMN branding_config TEXT"),
    ("jobs", "publishing_config", "ALTER TABLE jobs ADD COLUMN publishing_config TEXT"),
    ("jobs", "publishing_pack_path", "ALTER TABLE jobs ADD COLUMN publishing_pack_path TEXT"),
    ("jobs", "publish_ready_video_path", "ALTER TABLE jobs ADD COLUMN publish_ready_video_path TEXT"),
    ("jobs", "publishing_pack_status", "ALTER TABLE jobs ADD COLUMN publishing_pack_status TEXT DEFAULT 'disabled'"),
    ("jobs", "publishing_pack_error", "ALTER TABLE jobs ADD COLUMN publishing_pack_error TEXT"),
    # TTS voice override (None = pick the language's default voice, see
    # tts.voices.VOICE_OPTIONS).
    ("jobs", "tts_voice", "ALTER TABLE jobs ADD COLUMN tts_voice TEXT"),
    # "Chỉnh sửa phụ đề trước khi render" opt-in: when set, the job stops
    # at status='review' after translation instead of rendering straight
    # through, and `review_state_json` holds a serialized
    # PreparedLocalization (see orchestrator.service.prepared_localization_
    # to_dict) so a later request can resume rendering with edited text.
    ("jobs", "review_mode", "ALTER TABLE jobs ADD COLUMN review_mode INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "review_state_json", "ALTER TABLE jobs ADD COLUMN review_state_json TEXT"),
    # Current translated segments as [{start,end,text}, ...] JSON — the
    # ORIGINAL machine translation once prepare_for_review() finishes, then
    # overwritten with whatever the person edited it to before they hit
    # "Render". Also what GET .../subtitles.srt is generated from.
    ("jobs", "segments_json", "ALTER TABLE jobs ADD COLUMN segments_json TEXT"),
    ("jobs", "source_segments_json", "ALTER TABLE jobs ADD COLUMN source_segments_json TEXT"),
    # Post-render automated sanity-check warnings (see
    # render.quality_check.analyze_output_quality), as a JSON list of
    # human-readable strings. Empty/NULL = no warnings triggered.
    ("jobs", "qc_warnings_json", "ALTER TABLE jobs ADD COLUMN qc_warnings_json TEXT"),
    # Animated subtitle configuration (effect, style, effect_params) as JSON.
    ("jobs", "animated_subtitle_config", "ALTER TABLE jobs ADD COLUMN animated_subtitle_config TEXT"),
    # Video template configuration (template, transition, color_effect, etc.) as JSON.
    ("jobs", "video_template_config", "ALTER TABLE jobs ADD COLUMN video_template_config TEXT"),
    ("jobs", "remix_enabled", "ALTER TABLE jobs ADD COLUMN remix_enabled INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "remix_platforms_json", "ALTER TABLE jobs ADD COLUMN remix_platforms_json TEXT"),
    ("jobs", "remix_goal", "ALTER TABLE jobs ADD COLUMN remix_goal TEXT DEFAULT 'viral'"),
    ("jobs", "remix_strength", "ALTER TABLE jobs ADD COLUMN remix_strength TEXT DEFAULT 'balanced'"),
    ("jobs", "subtitle_offset_seconds", "ALTER TABLE jobs ADD COLUMN subtitle_offset_seconds REAL DEFAULT 0.0"),
    # Persist the downloaded source so completed jobs can offer a secure,
    # owner-scoped Before/After comparison instead of only the final render.
    ("jobs", "source_video_path", "ALTER TABLE jobs ADD COLUMN source_video_path TEXT"),
    # Video transformation configuration (flip, border, split-screen, randomization) as JSON.
    ("jobs", "transform_config", "ALTER TABLE jobs ADD COLUMN transform_config TEXT"),
    ("jobs", "processing_mode", "ALTER TABLE jobs ADD COLUMN processing_mode TEXT DEFAULT 'fast'"),
    ("jobs", "tts_provider", "ALTER TABLE jobs ADD COLUMN tts_provider TEXT DEFAULT 'edge'"),
    ("jobs", "tts_style", "ALTER TABLE jobs ADD COLUMN tts_style TEXT DEFAULT 'natural'"),
    ("jobs", "tts_model", "ALTER TABLE jobs ADD COLUMN tts_model TEXT"),
    ("jobs", "translation_mode", "ALTER TABLE jobs ADD COLUMN translation_mode TEXT DEFAULT 'faithful'"),
    ("jobs", "translation_model", "ALTER TABLE jobs ADD COLUMN translation_model TEXT"),
    ("jobs", "translation_tone", "ALTER TABLE jobs ADD COLUMN translation_tone TEXT DEFAULT 'natural'"),
    ("jobs", "translation_audience", "ALTER TABLE jobs ADD COLUMN translation_audience TEXT"),
    ("jobs", "translation_glossary", "ALTER TABLE jobs ADD COLUMN translation_glossary TEXT"),
    # Upload video audio configuration
    ("jobs", "keep_original_audio", "ALTER TABLE jobs ADD COLUMN keep_original_audio INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "background_music_strategy",
     "ALTER TABLE jobs ADD COLUMN background_music_strategy TEXT NOT NULL DEFAULT 'deterministic'"),
    ("channel_scan_states", "scan_version", "ALTER TABLE channel_scan_states ADD COLUMN scan_version INTEGER NOT NULL DEFAULT 1"),
    # Content OS migrations - add missing columns
    ("content_os_projects", "channel_id", "ALTER TABLE content_os_projects ADD COLUMN channel_id INTEGER"),
    ("content_os_projects", "mode", "ALTER TABLE content_os_projects ADD COLUMN mode TEXT NOT NULL DEFAULT 'ai_video'"),
    ("content_os_projects", "objective", "ALTER TABLE content_os_projects ADD COLUMN objective TEXT"),
    ("content_os_projects", "target_platform",
     "ALTER TABLE content_os_projects ADD COLUMN target_platform TEXT NOT NULL DEFAULT 'youtube_shorts'"),
    ("content_os_projects", "target_duration_seconds",
     "ALTER TABLE content_os_projects ADD COLUMN target_duration_seconds INTEGER NOT NULL DEFAULT 45"),
    ("content_os_projects", "target_language",
     "ALTER TABLE content_os_projects ADD COLUMN target_language TEXT NOT NULL DEFAULT 'vi'"),
    ("content_os_projects", "content_style", "ALTER TABLE content_os_projects ADD COLUMN content_style TEXT"),
    ("content_os_projects", "visual_style", "ALTER TABLE content_os_projects ADD COLUMN visual_style TEXT"),
    ("content_os_projects", "voice_id", "ALTER TABLE content_os_projects ADD COLUMN voice_id TEXT"),
    ("content_os_projects", "subtitle_style_id", "ALTER TABLE content_os_projects ADD COLUMN subtitle_style_id TEXT"),
    ("content_os_projects", "background_music_enabled",
     "ALTER TABLE content_os_projects ADD COLUMN background_music_enabled INTEGER NOT NULL DEFAULT 0"),
    ("content_os_projects", "user_instructions", "ALTER TABLE content_os_projects ADD COLUMN user_instructions TEXT"),
    ("content_os_projects", "settings_json", "ALTER TABLE content_os_projects ADD COLUMN settings_json TEXT"),
    ("content_os_projects", "status",
     "ALTER TABLE content_os_projects ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
    # Trend Scanner tables (new feature, no migration needed for fresh installs)
]


@dataclass
class TrendScan:
    id: str
    user_id: int
    topic: str
    platforms: List[str]
    providers: List[str]
    status: str = "pending"
    progress_note: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    max_results: int = 20
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["platforms_json"] = json.dumps(d.pop("platforms", []))
        d["providers_json"] = json.dumps(d.pop("providers", []))
        d["warnings_json"] = json.dumps(d.pop("warnings", []))
        return d


@dataclass
class TrendItem:
    id: Optional[int]
    scan_id: str
    user_id: int
    platform: str
    provider: str
    source_url: str
    title: Optional[str] = None
    author: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    published_at: Optional[str] = None
    trend_score: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)
    download_status: str = "found"
    local_path: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["raw_json"] = json.dumps(d.pop("raw", {}))
        return d


@dataclass
class Job:
    id: str
    user_id: int
    source_url: str
    target_language: str
    status: str
    progress_note: Optional[str]
    error: Optional[str]
    title: Optional[str]
    final_video_path: Optional[str]
    source_video_path: Optional[str]
    created_at: float
    updated_at: float
    source_channel_url: Optional[str] = None
    source_channel_title: Optional[str] = None
    source_channel_id: Optional[str] = None
    source_uploader: Optional[str] = None
    source_language: str = "auto"
    logo_path: Optional[str] = None
    logo_corner: str = "bottom_right"
    logo_size_px: int = 120
    branding_config: Optional[Dict[str, Any]] = None
    publishing_config: Optional[Dict[str, Any]] = None
    publishing_pack_path: Optional[str] = None
    publish_ready_video_path: Optional[str] = None
    publishing_pack_status: str = "disabled"
    publishing_pack_error: Optional[str] = None
    tts_voice: Optional[str] = None
    review_mode: int = 0
    review_state_json: Optional[str] = None
    segments_json: Optional[str] = None
    source_segments_json: Optional[str] = None
    qc_warnings_json: Optional[str] = None
    animated_subtitle_config: Optional[Dict[str, Any]] = None
    video_template_config: Optional[Dict[str, Any]] = None
    transform_config: Optional[Dict[str, Any]] = None
    processing_mode: str = "fast"
    tts_provider: str = "edge"
    tts_style: str = "natural"
    tts_model: Optional[str] = None
    translation_mode: str = "faithful"
    translation_model: Optional[str] = None
    translation_tone: str = "natural"
    translation_audience: Optional[str] = None
    translation_glossary: Optional[str] = None
    remix_enabled: int = 0
    remix_platforms_json: Optional[str] = None
    remix_goal: str = "viral"
    remix_strength: str = "balanced"
    subtitle_offset_seconds: float = 0.0
    keep_original_audio: int = 0
    background_music_strategy: str = "deterministic"

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["is_content_os"] = (
                str(self.source_url or "").startswith("content_os://")
                or str(self.source_language or "").startswith("content_os")
        )
        d["has_video"] = bool(self.final_video_path and Path(self.final_video_path).exists())
        d["has_publishing_pack"] = bool(
            self.publishing_pack_path and Path(self.publishing_pack_path).is_dir()
        )
        d["has_publish_ready_video"] = bool(
            self.publish_ready_video_path and Path(self.publish_ready_video_path).is_file()
        )
        # review_state_json is an internal implementation detail (a
        # serialized PreparedLocalization, can be sizeable) — not useful to
        # the frontend and not something to leak. segments_json IS useful
        # to the frontend (the review-editor reads it) so parse it into a
        # real list rather than making every caller json.loads() it.
        d.pop("review_state_json", None)
        segments_json = d.pop("segments_json", None)
        d["segments"] = json.loads(segments_json) if segments_json else None
        source_segments_json = d.pop("source_segments_json", None)
        d["source_segments"] = json.loads(source_segments_json) if source_segments_json else None
        qc_warnings_json = d.pop("qc_warnings_json", None)
        d["qc_warnings"] = json.loads(qc_warnings_json) if qc_warnings_json else []
        remix_platforms_json = d.pop("remix_platforms_json", None)
        d["remix_platforms"] = json.loads(remix_platforms_json) if remix_platforms_json else []
        return d


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            existing_cols = {
                table: {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for table in
                ("users", "jobs", "content_os_projects", "content_os_channels", "content_os_runs", "content_os_steps",
                 "content_os_artifacts", "content_os_sources", "content_os_reviews", "content_os_approvals",
                 "content_os_memories", "channel_scan_states", "channel_scan_videos")
            }
            migrated_legacy_projects = (
                    "target_platforms_json" in existing_cols.get("content_os_projects", set())
                    and "target_platform" not in existing_cols.get("content_os_projects", set())
            )
            ran_is_admin_migration = False
            for table, column, ddl in _MIGRATIONS:
                if column not in existing_cols.get(table, set()):
                    conn.execute(ddl)
                    if column == "is_admin":
                        ran_is_admin_migration = True

            if migrated_legacy_projects:
                # The first Content OS schema stored platforms as a JSON list.
                # Preserve that value when adding the canonical singular column;
                # otherwise SQLite's ADD COLUMN default silently changes every
                # existing project to youtube_shorts.
                rows = conn.execute(
                    "SELECT id, target_platforms_json FROM content_os_projects"
                ).fetchall()
                for row in rows:
                    try:
                        platforms = json.loads(row["target_platforms_json"] or "[]")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        platforms = []
                    if isinstance(platforms, list) and platforms and isinstance(platforms[0], str):
                        conn.execute(
                            "UPDATE content_os_projects SET target_platform = ? WHERE id = ?",
                            (platforms[0], row["id"]),
                        )

            if ran_is_admin_migration:
                # Upgrading a database created before admin/credits existed:
                # nobody has is_admin=1 yet (the column just got added with
                # a default of 0), which would lock whoever was already
                # using this server out of the new admin dashboard. Promote
                # the earliest-created account — i.e. whoever originally
                # set this server up — so continuity is preserved.
                any_admin = conn.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
                if not any_admin:
                    first_user = conn.execute(
                        "SELECT id FROM users ORDER BY created_at ASC LIMIT 1"
                    ).fetchone()
                    if first_user:
                        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first_user["id"],))

    # ---- users ----
    def create_user(
            self, username: str, password_hash: str, is_admin: bool = False,
            credits: int = 10, email: Optional[str] = None, phone: Optional[str] = None,
            referred_by_user_id: Optional[int] = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, credits, is_admin, email, phone, "
                "referral_code, referred_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username, password_hash, credits, int(is_admin), email, phone,
                 self._new_referral_code(conn), referred_by_user_id, time.time()),
            )
            return cur.lastrowid

    @staticmethod
    def _new_referral_code(conn: sqlite3.Connection) -> str:
        """Short, unique, easy-to-type-or-paste-into-a-URL referral code."""
        import secrets
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I ambiguity
        for _ in range(20):
            code = "".join(secrets.choice(alphabet) for _ in range(7))
            if not conn.execute("SELECT 1 FROM users WHERE referral_code = ?", (code,)).fetchone():
                return code
        return secrets.token_hex(6).upper()  # astronomically unlikely fallback

    def get_user_by_referral_code(self, code: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE referral_code = ?", (code.strip().upper(),))
            return cur.fetchone()

    def create_user_oauth(
            self, username: str, oauth_provider: str, oauth_id: str,
            email: Optional[str] = None, is_admin: bool = False, credits: int = 10,
    ) -> int:
        """
        Create an account for someone who signed up/in via "Sign in with
        Google/GitHub/Facebook" rather than a username+password form.

        `password_hash` still gets a real (but unusable/never-shared)
        bcrypt hash of a random token — rather than NULL — purely so this
        works unmodified against an existing database that still has the
        original `password_hash TEXT NOT NULL` constraint from before OAuth
        login existed; the value itself can never be used to log in since
        nobody knows it.
        """
        import secrets
        from .auth import hash_password
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, credits, is_admin, email, "
                "oauth_provider, oauth_id, referral_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username, hash_password(secrets.token_urlsafe(32)), credits, int(is_admin),
                 email, oauth_provider, oauth_id, self._new_referral_code(conn), time.time()),
            )
            return cur.lastrowid

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            return cur.fetchone()

    def get_user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
            return cur.fetchone()

    def get_user_by_phone(self, phone: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,))
            return cur.fetchone()

    def get_user_by_identifier(self, identifier: str) -> Optional[sqlite3.Row]:
        """Look up a user by whichever of username/email/phone matches —
        used at login time so one input box can accept any of the three."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM users WHERE username = ? OR lower(email) = lower(?) OR phone = ?",
                (identifier, identifier, identifier),
            )
            return cur.fetchone()

    def get_user_by_oauth(self, provider: str, oauth_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM users WHERE oauth_provider = ? AND oauth_id = ?",
                (provider, oauth_id),
            )
            return cur.fetchone()

    def get_user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return cur.fetchone()

    def any_users_exist(self) -> bool:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM users")
            return cur.fetchone()["c"] > 0

    def list_users(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users ORDER BY created_at ASC")
            return cur.fetchall()

    def adjust_credits(self, user_id: int, delta: int) -> int:
        """Add (or, with a negative delta, subtract) credits. Returns the new balance."""
        with self._connect() as conn:
            conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (delta, user_id))
            row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
            return row["credits"] if row else 0

    def set_credits(self, user_id: int, value: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (value, user_id))

    def set_admin(self, user_id: int, is_admin: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))

    def create_user_by_admin(self, username: str, password_hash: str, credits: int = 10) -> int:
        return self.create_user(username, password_hash, is_admin=False, credits=credits)

    # ---- jobs ----
    def create_job(
            self, user_id: int, source_url: str, target_language: str,
            source_language: str = "auto", logo_path: Optional[str] = None,
            logo_corner: str = "bottom_right", logo_size_px: int = 120,
            branding_config: Optional[Dict[str, Any]] = None,
            publishing_config: Optional[Dict[str, Any]] = None,
            tts_voice: Optional[str] = None, review_mode: bool = False,
            animated_subtitle_config: Optional[Dict[str, Any]] = None,
            video_template_config: Optional[Dict[str, Any]] = None,
            transform_config: Optional[Dict[str, Any]] = None,
            processing_mode: str = "fast",
            tts_provider: str = "edge",
            tts_style: str = "natural",
            tts_model: Optional[str] = None,
            translation_mode: str = "faithful",
            translation_model: Optional[str] = None,
            translation_tone: str = "natural",
            translation_audience: Optional[str] = None,
            translation_glossary: Optional[str] = None,
            remix_enabled: bool = False,
            remix_platforms: Optional[List[str]] = None,
            remix_goal: str = "viral",
            remix_strength: str = "balanced",
            subtitle_offset_seconds: float = 0.0,
            keep_original_audio: int = 0,
            background_music_strategy: str = "deterministic",
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        job = Job(
            id=job_id, user_id=user_id, source_url=source_url,
            target_language=target_language, status="queued",
            progress_note="Đã xếp hàng chờ xử lý", error=None, title=None,
            final_video_path=None, source_video_path=None, created_at=now, updated_at=now,
            source_language=source_language, logo_path=logo_path,
            logo_corner=logo_corner, logo_size_px=logo_size_px,
            branding_config=branding_config,
            publishing_config=publishing_config,
            publishing_pack_status=("pending" if (publishing_config or {}).get("enabled") else "disabled"),
            tts_voice=tts_voice, review_mode=int(review_mode),
            animated_subtitle_config=animated_subtitle_config,
            video_template_config=video_template_config,
            transform_config=transform_config,
            processing_mode=processing_mode,
            tts_provider=tts_provider,
            tts_style=tts_style,
            tts_model=tts_model,
            translation_mode=translation_mode,
            translation_model=translation_model,
            translation_tone=translation_tone,
            translation_audience=translation_audience,
            translation_glossary=translation_glossary,
            remix_enabled=int(remix_enabled),
            remix_platforms_json=json.dumps(remix_platforms or [], ensure_ascii=False),
            remix_goal=remix_goal,
            remix_strength=remix_strength,
            subtitle_offset_seconds=float(subtitle_offset_seconds or 0.0),
            keep_original_audio=keep_original_audio,
            background_music_strategy=background_music_strategy,
        )
        columns = (
            "id", "user_id", "source_url", "target_language", "source_language", "status",
            "progress_note", "error", "title", "final_video_path", "logo_path", "logo_corner",
            "logo_size_px", "branding_config", "publishing_config", "publishing_pack_path",
            "publish_ready_video_path", "publishing_pack_status", "publishing_pack_error",
            "tts_voice", "review_mode", "review_state_json", "segments_json",
            "qc_warnings_json", "created_at", "updated_at", "animated_subtitle_config",
            "video_template_config", "transform_config", "source_video_path", "source_segments_json",
            "processing_mode", "tts_provider", "tts_style", "translation_mode", "translation_tone",
            "translation_audience", "translation_glossary", "tts_model", "translation_model",
            "remix_enabled", "remix_platforms_json", "remix_goal", "remix_strength",
            "subtitle_offset_seconds", "keep_original_audio", "background_music_strategy",
        )
        values = (
            job.id, job.user_id, job.source_url, job.target_language, job.source_language,
            job.status, job.progress_note, job.error, job.title, job.final_video_path,
            job.logo_path, job.logo_corner, job.logo_size_px,
            json.dumps(job.branding_config, ensure_ascii=False) if job.branding_config else None,
            json.dumps(job.publishing_config, ensure_ascii=False) if job.publishing_config else None,
            job.publishing_pack_path, job.publish_ready_video_path, job.publishing_pack_status,
            job.publishing_pack_error, job.tts_voice, job.review_mode, job.review_state_json,
            job.segments_json, job.qc_warnings_json,
            job.created_at, job.updated_at,
            json.dumps(job.animated_subtitle_config) if job.animated_subtitle_config else None,
            json.dumps(job.video_template_config) if job.video_template_config else None,
            json.dumps(job.transform_config) if job.transform_config else None,
            job.source_video_path, job.source_segments_json,
            job.processing_mode, job.tts_provider, job.tts_style,
            job.translation_mode, job.translation_tone, job.translation_audience,
            job.translation_glossary, job.tts_model, job.translation_model,
            job.remix_enabled, job.remix_platforms_json, job.remix_goal, job.remix_strength,
            job.subtitle_offset_seconds, job.keep_original_audio, job.background_music_strategy,
        )
        placeholders = ",".join("?" for _ in values)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO jobs ({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
        return job

    def retry_job(self, job_id: str, user_id: int) -> Optional[Job]:
        """Reset a failed job in place and return the same history entry.

        Command settings and source metadata are preserved. Attempt-specific
        output is cleared. The conditional UPDATE also makes a double-click
        safe: only the first request can move ``error`` to ``queued``.
        """
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET "
                "status = 'queued', progress_note = ?, error = NULL, title = NULL, "
                "final_video_path = NULL, source_video_path = NULL, "
                "review_state_json = NULL, segments_json = NULL, source_segments_json = NULL, "
                "qc_warnings_json = NULL, publishing_pack_path = NULL, "
                "publish_ready_video_path = NULL, publishing_pack_status = CASE "
                "WHEN COALESCE(json_extract(publishing_config, '$.enabled'), 0) = 1 "
                "THEN 'pending' ELSE 'disabled' END, "
                "publishing_pack_error = NULL, created_at = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ? AND status = 'error'",
                ("Đã xếp hàng chạy lại", now, now, job_id, user_id),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    # ---- publishing channel profiles ----
    def list_publishing_profiles(self, user_id: int) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publishing_channel_profiles WHERE user_id = ? "
                "ORDER BY is_default DESC, updated_at DESC, name ASC",
                (user_id,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            try:
                profile = json.loads(row["profile_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                profile = {}
            result.append({
                "id": row["id"],
                "name": row["name"],
                "is_default": bool(row["is_default"]),
                "profile": profile if isinstance(profile, dict) else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return result

    def get_publishing_profile(self, user_id: int, profile_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM publishing_channel_profiles WHERE user_id = ? AND id = ?",
                (user_id, str(profile_id)),
            ).fetchone()
        if not row:
            return None
        try:
            profile = json.loads(row["profile_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            profile = {}
        return {
            "id": row["id"], "name": row["name"], "is_default": bool(row["is_default"]),
            "profile": profile if isinstance(profile, dict) else {},
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def save_publishing_profile(
            self, user_id: int, *, name: str, profile: Dict[str, Any],
            profile_id: Optional[str] = None, is_default: bool = False,
    ) -> Dict[str, Any]:
        profile_id = str(profile_id or uuid.uuid4().hex)
        clean_name = " ".join(str(name or "Hồ sơ kênh").split())[:100] or "Hồ sơ kênh"
        now = time.time()
        payload = json.dumps(profile or {}, ensure_ascii=False)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM publishing_channel_profiles WHERE user_id = ? AND id = ?",
                (user_id, profile_id),
            ).fetchone()
            if is_default:
                conn.execute(
                    "UPDATE publishing_channel_profiles SET is_default = 0 WHERE user_id = ?",
                    (user_id,),
                )
            if existing:
                conn.execute(
                    "UPDATE publishing_channel_profiles SET name = ?, profile_json = ?, "
                    "is_default = ?, updated_at = ? WHERE user_id = ? AND id = ?",
                    (clean_name, payload, int(is_default), now, user_id, profile_id),
                )
            else:
                conn.execute(
                    "INSERT INTO publishing_channel_profiles "
                    "(id, user_id, name, profile_json, is_default, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (profile_id, user_id, clean_name, payload, int(is_default), now, now),
                )
        saved = self.get_publishing_profile(user_id, profile_id)
        assert saved is not None
        return saved

    def delete_publishing_profile(self, user_id: int, profile_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM publishing_channel_profiles WHERE user_id = ? AND id = ?",
                (user_id, str(profile_id)),
            )
            return cur.rowcount > 0

    def set_default_publishing_profile(self, user_id: int, profile_id: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE publishing_channel_profiles SET is_default = 0 WHERE user_id = ?", (user_id,))
            if profile_id:
                cur = conn.execute(
                    "UPDATE publishing_channel_profiles SET is_default = 1, updated_at = ? "
                    "WHERE user_id = ? AND id = ?",
                    (time.time(), user_id, str(profile_id)),
                )
                if cur.rowcount <= 0:
                    raise ValueError("Publishing profile not found")

    def latest_job_publishing_config(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT publishing_config FROM jobs WHERE user_id = ? AND publishing_config IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        if not row or not row["publishing_config"]:
            return None
        try:
            data = json.loads(row["publishing_config"])
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    # ---- provider settings ----
    def upsert_provider_settings(
            self,
            user_id: int,
            provider: str,
            api_key: Optional[str] = None,
            api_secret: Optional[str] = None,
            default_model: Optional[str] = None,
            default_voice: Optional[str] = None,
            extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        provider = provider.strip().lower()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM user_provider_settings WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_provider_settings SET api_key = COALESCE(?, api_key), "
                    "api_secret = COALESCE(?, api_secret), default_model = ?, default_voice = ?, "
                    "extra_json = ?, updated_at = ? WHERE user_id = ? AND provider = ?",
                    (
                        api_key, api_secret, default_model, default_voice,
                        json.dumps(extra or {}, ensure_ascii=False), now, user_id, provider,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO user_provider_settings "
                    "(user_id, provider, api_key, api_secret, default_model, default_voice, extra_json, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        user_id, provider, api_key, api_secret, default_model, default_voice,
                        json.dumps(extra or {}, ensure_ascii=False), now, now,
                    ),
                )

    def delete_provider_settings(self, user_id: int, provider: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM user_provider_settings WHERE user_id = ? AND provider = ?",
                (user_id, provider.strip().lower()),
            )

    def get_provider_settings(self, user_id: int, provider: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_provider_settings WHERE user_id = ? AND provider = ?",
                (user_id, provider.strip().lower()),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["extra"] = json.loads(data.pop("extra_json") or "{}")
        except json.JSONDecodeError:
            data["extra"] = {}
        return data

    def list_provider_settings(self, user_id: int) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_provider_settings WHERE user_id = ? ORDER BY provider ASC",
                (user_id,),
            ).fetchall()
        settings = []
        for row in rows:
            data = dict(row)
            try:
                data["extra"] = json.loads(data.pop("extra_json") or "{}")
            except json.JSONDecodeError:
                data["extra"] = {}
            settings.append(data)
        return settings

    def set_job_segments(self, job_id: str, segments: List[Dict[str, Any]]) -> None:
        """Overwrite the current translated segments (used both when
        prepare_for_review() first produces them, and when the person
        edits/saves changes before rendering)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET segments_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(segments, ensure_ascii=False), time.time(), job_id),
            )

    def set_job_source_segments(self, job_id: str, segments: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET source_segments_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(segments, ensure_ascii=False), time.time(), job_id),
            )

    def set_job_review_state(self, job_id: str, review_state: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET review_state_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(review_state, ensure_ascii=False), time.time(), job_id),
            )

    def ensure_referral_code(self, user_id: int) -> str:
        """Backfill a referral_code for accounts created before this feature
        existed (NULL in the DB). Idempotent — a no-op once set."""
        with self._connect() as conn:
            row = conn.execute("SELECT referral_code FROM users WHERE id = ?", (user_id,)).fetchone()
            if row and row["referral_code"]:
                return row["referral_code"]
            code = self._new_referral_code(conn)
            conn.execute("UPDATE users SET referral_code = ? WHERE id = ?", (code, user_id))
            return code

    def set_job_qc_warnings(self, job_id: str, warnings: List[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET qc_warnings_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(warnings, ensure_ascii=False), time.time(), job_id),
            )

    def user_stats(self, user_id: int) -> Dict[str, Any]:
        """Personal stats for the logged-in user's own history — total
        jobs, breakdown by status, and an estimated total credits spent
        (JOB_COST_CREDITS is applied per submission at the app layer, so
        this counts submissions rather than re-deriving the cost here)."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM jobs WHERE user_id = ?", (user_id,)).fetchone()["c"]
            by_status = {
                row["status"]: row["c"] for row in conn.execute(
                    "SELECT status, COUNT(*) c FROM jobs WHERE user_id = ? GROUP BY status", (user_id,)
                )
            }
        done = by_status.get("done", 0)
        error = by_status.get("error", 0)
        finished = done + error
        return {
            "total_jobs": total,
            "by_status": by_status,
            "success_rate": round(done / finished * 100, 1) if finished else None,
        }

    # ---- feedback ----
    def create_feedback(self, user_id: Optional[int], message: str, page: Optional[str] = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO feedback (user_id, message, page, created_at) VALUES (?,?,?,?)",
                (user_id, message, page, time.time()),
            )
            return cur.lastrowid

    def list_feedback(self, limit: int = 200) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT f.*, u.username, u.email, u.phone FROM feedback f LEFT JOIN users u ON u.id = f.user_id "
                "ORDER BY f.created_at DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()

    # ---- top-up requests ----
    def create_top_up_request(
            self, user_id: int, credits: int, amount_vnd: int,
            payment_method: str, note: Optional[str] = None,
    ) -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO top_up_requests "
                "(user_id, credits, amount_vnd, payment_method, note, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (user_id, credits, amount_vnd, payment_method, note, "pending", now, now),
            )
            return cur.lastrowid

    def list_top_up_requests_for_user(self, user_id: int, limit: int = 50) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM top_up_requests WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            return cur.fetchall()

    def list_top_up_requests(self, limit: int = 200) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT r.*, u.username FROM top_up_requests r "
                "LEFT JOIN users u ON u.id = r.user_id "
                "ORDER BY r.created_at DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()

    def approve_top_up_request(self, request_id: int, admin_note: Optional[str] = None) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM top_up_requests WHERE id = ?", (request_id,)).fetchone()
            if not row or row["status"] != "pending":
                return None
            conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (row["credits"], row["user_id"]))
            conn.execute(
                "UPDATE top_up_requests SET status = 'approved', admin_note = ?, updated_at = ? WHERE id = ?",
                (admin_note, time.time(), request_id),
            )
            return conn.execute("SELECT * FROM top_up_requests WHERE id = ?", (request_id,)).fetchone()

    def reject_top_up_request(self, request_id: int, admin_note: Optional[str] = None) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM top_up_requests WHERE id = ?", (request_id,)).fetchone()
            if not row or row["status"] != "pending":
                return None
            conn.execute(
                "UPDATE top_up_requests SET status = 'rejected', admin_note = ?, updated_at = ? WHERE id = ?",
                (admin_note, time.time(), request_id),
            )
            return conn.execute("SELECT * FROM top_up_requests WHERE id = ?", (request_id,)).fetchone()

    def set_job_publishing_config(
            self, job_id: str, publishing_config: Optional[Dict[str, Any]],
    ) -> None:
        payload = json.dumps(publishing_config, ensure_ascii=False) if publishing_config else None
        status = "pending" if (publishing_config or {}).get("enabled") else "disabled"
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET publishing_config = ?, publishing_pack_status = ?, updated_at = ? WHERE id = ?",
                (payload, status, time.time(), job_id),
            )

    def set_job_branding_config(
            self, job_id: str, branding_config: Optional[Dict[str, Any]],
    ) -> None:
        """Persist normalized branding config for an existing job.

        This is also used as a compatibility fallback when app.py is reloaded
        before a newer create_job signature is visible to the running process.
        """
        payload = (
            json.dumps(branding_config, ensure_ascii=False)
            if branding_config else None
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET branding_config = ?, updated_at = ? WHERE id = ?",
                (payload, time.time(), job_id),
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)

    def fail_interrupted_jobs(self, refund_credits: int = 0) -> int:
        """Resolve in-process jobs orphaned by a server restart."""
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, user_id FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    "UPDATE jobs SET status = 'error', progress_note = ?, error = ?, updated_at = ? "
                    "WHERE id = ? AND status IN ('queued', 'running')",
                    (
                        "Tiến trình bị gián đoạn do server khởi động lại",
                        "Job không thể tiếp tục sau khi server khởi động lại. Hãy bấm Thử lại.",
                        now, row["id"],
                    ),
                )
                if cursor.rowcount and refund_credits > 0:
                    conn.execute(
                        "UPDATE users SET credits = credits + ? WHERE id = ?",
                        (refund_credits, row["user_id"]),
                    )
            return len(rows)

    _JOB_FIELDS = {f.name for f in dataclasses.fields(Job)}

    @classmethod
    def _row_to_job(cls, row: sqlite3.Row) -> Job:
        """
        Build a `Job` from a DB row, silently ignoring any column that
        isn't a field on the current `Job` dataclass.

        Without this, a database that has ever had columns added by a
        different/older version of this app (e.g. a stray
        `enable_anti_copyright` column from an earlier build) makes EVERY
        job-listing call crash with `Job.__init__() got an unexpected
        keyword argument ...` — the row itself is fine, only unknown-to-us
        columns need to be dropped before constructing the dataclass.
        """
        row_dict = dict(row)
        unknown = set(row_dict) - cls._JOB_FIELDS
        if unknown:
            for key in unknown:
                row_dict.pop(key, None)
        # Deserialize JSON fields
        if row_dict.get("animated_subtitle_config"):
            row_dict["animated_subtitle_config"] = json.loads(row_dict["animated_subtitle_config"])
        if row_dict.get("video_template_config"):
            row_dict["video_template_config"] = json.loads(row_dict["video_template_config"])
        if row_dict.get("transform_config"):
            row_dict["transform_config"] = json.loads(row_dict["transform_config"])
        if row_dict.get("branding_config"):
            row_dict["branding_config"] = json.loads(row_dict["branding_config"])
        if row_dict.get("publishing_config"):
            row_dict["publishing_config"] = json.loads(row_dict["publishing_config"])
        return Job(**row_dict)

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return self._row_to_job(row) if row else None

    def existing_source_urls_for_user(self, user_id: int, urls: List[str]) -> set[str]:
        """Return exact source URLs already present in this user's history.

        Used by channel mode to avoid re-downloading/re-processing videos that
        have already been submitted. Queries are chunked to stay below
        SQLite's bound-parameter limit.
        """
        cleaned = [str(url).strip() for url in urls if str(url).strip()]
        if not cleaned:
            return set()
        found: set[str] = set()
        with self._connect() as conn:
            for offset in range(0, len(cleaned), 800):
                chunk = cleaned[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT source_url FROM jobs WHERE user_id = ? "
                    f"AND source_url IN ({placeholders})",
                    [user_id, *chunk],
                ).fetchall()
                found.update(str(row["source_url"]) for row in rows)
        return found

    def set_job_source_channel(
            self,
            job_id: str,
            user_id: int,
            *,
            channel_url: str = "",
            channel_title: str = "",
            channel_id: str = "",
            uploader: str = "",
    ) -> bool:
        """Attach source-channel provenance to a job created from channel mode."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET source_channel_url = ?, source_channel_title = ?, "
                "source_channel_id = ?, source_uploader = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (
                    str(channel_url or "").strip() or None,
                    str(channel_title or "").strip() or None,
                    str(channel_id or "").strip() or None,
                    str(uploader or "").strip() or None,
                    time.time(), job_id, user_id,
                ),
            )
            return cur.rowcount > 0

    def get_channel_scan_state(self, user_id: int, canonical_url: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_scan_states WHERE user_id = ? AND canonical_url = ?",
                (user_id, str(canonical_url).strip()),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        if data.get("has_more") is not None:
            data["has_more"] = bool(data["has_more"])
        data["complete"] = bool(data.get("complete"))
        return data

    def get_channel_scan_video_ids(self, user_id: int, canonical_url: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT v.video_id FROM channel_scan_videos v "
                "JOIN channel_scan_states s ON s.id = v.state_id "
                "WHERE s.user_id = ? AND s.canonical_url = ?",
                (user_id, str(canonical_url).strip()),
            ).fetchall()
        return {str(row["video_id"]) for row in rows if row["video_id"]}

    def reset_channel_scan(self, user_id: int, canonical_url: str) -> bool:
        canonical_url = str(canonical_url).strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM channel_scan_states WHERE user_id = ? AND canonical_url = ?",
                (user_id, canonical_url),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM channel_scan_videos WHERE state_id = ?", (row["id"],))
            conn.execute("DELETE FROM channel_scan_states WHERE id = ?", (row["id"],))
            return True

    def merge_channel_scan_result(
            self,
            user_id: int,
            *,
            original_url: str,
            canonical_url: str,
            platform: str,
            channel_id: str = "",
            channel_title: str = "",
            videos: List[Dict[str, Any]],
            cursor: str = "",
            has_more: Optional[bool] = None,
            complete: bool = False,
            stop_reason: str = "",
            scan_source: str = "",
            network_pages: int = 0,
            last_error: str = "",
            scan_version: int = 2,
    ) -> Dict[str, Any]:
        """Merge one scan into the user's persistent channel catalog.

        Video identity is primarily `video_id`/aweme_id and falls back to the
        canonical source URL. Existing rows are refreshed without changing
        their original catalog position, so repeated scans never create nine
        duplicate entries at the top.
        """
        now = time.time()
        canonical_url = str(canonical_url).strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_scan_states WHERE user_id = ? AND canonical_url = ?",
                (user_id, canonical_url),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO channel_scan_states ("
                    "user_id, platform, original_url, canonical_url, channel_id, channel_title, "
                    "cursor, has_more, complete, status, stop_reason, scan_source, network_pages, "
                    "last_error, total_discovered, scan_version, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        user_id, platform, original_url, canonical_url, channel_id, channel_title,
                        cursor or None, None if has_more is None else int(bool(has_more)),
                        int(bool(complete)), "complete" if complete else "incomplete",
                        stop_reason or None, scan_source or None, int(network_pages or 0),
                        last_error or None, int(scan_version or 2), now, now,
                    ),
                )
                state_id = int(cur.lastrowid)
                next_position = 0
            else:
                state_id = int(row["id"])
                next_position_row = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 AS next_position "
                    "FROM channel_scan_videos WHERE state_id = ?",
                    (state_id,),
                ).fetchone()
                next_position = int(next_position_row["next_position"] or 0)

            new_count = 0
            seen_in_request: set[tuple[str, str]] = set()
            for raw_video in videos or []:
                source_url = str(raw_video.get("source_url") or "").strip()
                video_id = str(raw_video.get("video_id") or raw_video.get("aweme_id") or "").strip()
                if not video_id and source_url:
                    video_id = source_url.rstrip("/").rsplit("/", 1)[-1]
                if not source_url or not video_id:
                    continue
                identity = (video_id, source_url)
                if identity in seen_in_request:
                    continue
                seen_in_request.add(identity)
                metadata_json = json.dumps(raw_video, ensure_ascii=False)
                existing = conn.execute(
                    "SELECT id FROM channel_scan_videos "
                    "WHERE state_id = ? AND (video_id = ? OR source_url = ?) LIMIT 1",
                    (state_id, video_id, source_url),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO channel_scan_videos ("
                        "state_id, video_id, source_url, metadata_json, position, discovered_at, last_seen_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (state_id, video_id, source_url, metadata_json, next_position, now, now),
                    )
                    next_position += 1
                    new_count += 1
                else:
                    conn.execute(
                        "UPDATE channel_scan_videos SET source_url = ?, metadata_json = ?, last_seen_at = ? "
                        "WHERE id = ?",
                        (source_url, metadata_json, now, existing["id"]),
                    )

                # Backfill provenance for older channel jobs when a newly
                # owner-verified catalog sees the same source URL again.
                conn.execute(
                    "UPDATE jobs SET source_channel_url = COALESCE(source_channel_url, ?), "
                    "source_channel_title = COALESCE(source_channel_title, ?), "
                    "source_channel_id = COALESCE(source_channel_id, ?), "
                    "source_uploader = COALESCE(source_uploader, ?), updated_at = updated_at "
                    "WHERE user_id = ? AND source_url = ?",
                    (
                        canonical_url or None,
                        channel_title or None,
                        channel_id or None,
                        str(raw_video.get("uploader") or "").strip() or None,
                        user_id,
                        source_url,
                    ),
                )

            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM channel_scan_videos WHERE state_id = ?",
                (state_id,),
            ).fetchone()
            total = int(total_row["total"] or 0)
            conn.execute(
                "UPDATE channel_scan_states SET platform = ?, original_url = ?, channel_id = ?, "
                "channel_title = ?, cursor = ?, has_more = ?, complete = ?, status = ?, "
                "stop_reason = ?, scan_source = ?, network_pages = ?, last_error = ?, "
                "total_discovered = ?, scan_version = ?, updated_at = ? WHERE id = ?",
                (
                    platform, original_url, channel_id or None, channel_title or None,
                    cursor or None, None if has_more is None else int(bool(has_more)),
                    int(bool(complete)), "complete" if complete else ("error" if last_error else "incomplete"),
                    stop_reason or None, scan_source or None, int(network_pages or 0),
                    last_error or None, total, int(scan_version or 2), now, state_id,
                ),
            )

        state = self.get_channel_scan_state(user_id, canonical_url) or {}
        state["new_count"] = new_count
        state["total_discovered"] = total
        return state

    def list_channel_scan_videos(
            self, user_id: int, canonical_url: str, limit: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = (
            "SELECT v.video_id, v.source_url, v.metadata_json, v.position, "
            "v.discovered_at, v.last_seen_at FROM channel_scan_videos v "
            "JOIN channel_scan_states s ON s.id = v.state_id "
            "WHERE s.user_id = ? AND s.canonical_url = ? ORDER BY v.position ASC"
        )
        params: List[Any] = [user_id, str(canonical_url).strip()]
        if int(limit or 0) > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        output: List[Dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            metadata["video_id"] = str(metadata.get("video_id") or row["video_id"] or "")
            metadata["source_url"] = str(metadata.get("source_url") or row["source_url"] or "")
            metadata["catalog_position"] = int(row["position"] or 0)
            output.append(metadata)
        return output

    def list_jobs_for_user(self, user_id: int, limit: int = 100) -> List[Job]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            return [self._row_to_job(row) for row in cur.fetchall()]

    def search_jobs_for_user(
            self,
            user_id: int,
            query: Optional[str] = None,
            status: Optional[str] = None,
            date_from: Optional[float] = None,
            date_to: Optional[float] = None,
            limit: int = 200,
    ) -> List[Job]:
        """
        Same as `list_jobs_for_user` but filterable — used by the history
        panel's search box (matches job title/source URL, case-insensitive)
        and date-range filter (both as Unix timestamps, inclusive).
        Always scoped to `user_id`, so one account can never see or search
        another account's history.
        """
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        if query:
            clauses.append("(title LIKE ? OR source_url LIKE ? OR source_channel_title LIKE ? OR source_channel_url LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like, like])
        if status:
            clauses.append("status = ?")
            params.append(status)
        if date_from is not None:
            clauses.append("created_at >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("created_at <= ?")
            params.append(date_to)

        where = " AND ".join(clauses)
        params.append(limit)
        with self._connect() as conn:
            cur = conn.execute(
                f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
            return [self._row_to_job(row) for row in cur.fetchall()]

    def delete_job(self, job_id: str, user_id: int) -> bool:
        """
        Delete a history entry. Scoped to `user_id` so one account can never
        delete another account's job even if it guesses/enumerates job ids.
        Returns True if a row was actually deleted.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_posts SET status='cancelled', updated_at=? "
                "WHERE job_id=? AND user_id=? AND status='pending'",
                (time.time(), job_id, user_id),
            )
            cur = conn.execute(
                "DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
            )
            return cur.rowcount > 0

    def delete_jobs(self, job_ids: List[str], user_id: int) -> int:
        ids = list(dict.fromkeys(job_ids))[:200]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE scheduled_posts SET status='cancelled', updated_at=? "
                f"WHERE user_id=? AND status='pending' AND job_id IN ({placeholders})",
                [time.time(), user_id, *ids],
            )
            cur = conn.execute(
                f"DELETE FROM jobs WHERE user_id = ? AND id IN ({placeholders})",
                [user_id, *ids],
            )
            return cur.rowcount

    def create_scheduled_post(self, user_id: int, job_id: str, platforms: List[str],
                              title: str, description: str, hashtags: List[str],
                              scheduled_at: float) -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO scheduled_posts (user_id,job_id,platforms_json,title,description,"
                "hashtags_json,scheduled_at,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user_id, job_id, json.dumps(platforms), title, description,
                 json.dumps(hashtags, ensure_ascii=False), scheduled_at, "pending", now, now),
            )
            return int(cur.lastrowid)

    def list_scheduled_posts(self, user_id: int) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM scheduled_posts WHERE user_id = ? ORDER BY scheduled_at DESC LIMIT 100",
                (user_id,),
            ).fetchall()

    def claim_due_scheduled_posts(self, now: float) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_posts WHERE status = 'pending' AND scheduled_at <= ? ORDER BY scheduled_at LIMIT 20",
                (now,),
            ).fetchall()
            claimed = []
            for row in rows:
                cur = conn.execute(
                    "UPDATE scheduled_posts SET status='processing', updated_at=? WHERE id=? AND status='pending'",
                    (now, row["id"]),
                )
                if cur.rowcount:
                    claimed.append(row)
            return claimed

    def recover_processing_scheduled_posts(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_posts SET status='error', result_json=?, updated_at=? WHERE status='processing'",
                (json.dumps({"message": "Server khởi động lại khi đang đăng; không tự thử lại để tránh đăng trùng."},
                            ensure_ascii=False), time.time()),
            )

    def finish_scheduled_post(self, post_id: int, status: str, result: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_posts SET status=?, result_json=?, updated_at=? WHERE id=?",
                (status, json.dumps(result, ensure_ascii=False), time.time(), post_id),
            )

    def cancel_scheduled_post(self, post_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE scheduled_posts SET status='cancelled', updated_at=? "
                "WHERE id=? AND user_id=? AND status='pending'",
                (time.time(), post_id, user_id),
            )
            return cur.rowcount > 0

    # ---- publish log ----
    def log_publish(self, job_id: str, platform: str, success: bool, message: str,
                    remote_url: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO publish_log (job_id, platform, success, message, remote_url, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, platform, int(success), message, remote_url, time.time()),
            )

    # ---- social accounts (per-user OAuth connections) ----
    def upsert_social_account(
            self, user_id: int, platform: str, access_token: Optional[str],
            refresh_token: Optional[str] = None, expires_at: Optional[float] = None,
            account_name: Optional[str] = None, account_ref: Optional[str] = None,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO social_accounts
                (user_id, platform, access_token, refresh_token, expires_at,
                 account_name, account_ref, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, platform) DO
                UPDATE SET
                    access_token=excluded.access_token,
                    refresh_token= COALESCE (excluded.refresh_token, social_accounts.refresh_token),
                    expires_at=excluded.expires_at,
                    account_name=excluded.account_name,
                    account_ref=excluded.account_ref,
                    updated_at=excluded.updated_at
                """,
                (user_id, platform, access_token, refresh_token, expires_at,
                 account_name, account_ref, now, now),
            )

    def get_social_account(self, user_id: int, platform: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM social_accounts WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )
            return cur.fetchone()

    def list_social_accounts(self, user_id: int) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM social_accounts WHERE user_id = ?", (user_id,))
            return cur.fetchall()

    def delete_social_account(self, user_id: int, platform: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM social_accounts WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )

    # ---- oauth state (CSRF protection for the connect redirect flow) ----
    def create_oauth_state(self, state: str, user_id: int, platform: str) -> None:
        now = time.time()
        with self._connect() as conn:
            # Sweep anything older than an hour — these are single-use and
            # short-lived, no reason to let abandoned ones pile up.
            conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (now - 3600,))
            conn.execute(
                "INSERT INTO oauth_states (state, user_id, platform, created_at) VALUES (?,?,?,?)",
                (state, user_id, platform, now),
            )

    def consume_oauth_state(self, state: str) -> Optional[sqlite3.Row]:
        """Look up and delete a state token in one call (single-use)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
            if row:
                conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            return row

    # ---- identity oauth state (CSRF protection for "Sign in with ..." login/register) ----
    def create_identity_oauth_state(self, state: str, provider: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM identity_oauth_states WHERE created_at < ?", (now - 3600,))
            conn.execute(
                "INSERT INTO identity_oauth_states (state, provider, created_at) VALUES (?,?,?)",
                (state, provider, now),
            )

    def consume_identity_oauth_state(self, state: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM identity_oauth_states WHERE state = ?", (state,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM identity_oauth_states WHERE state = ?", (state,))
            return row

    # ---- video presets ----
    def create_video_preset(
            self,
            user_id: int,
            name: str,
            template: str,
            transition: str,
            color_effect: str,
            audio_filters: Optional[Dict[str, Any]] = None,
            video_quality: Optional[str] = None,
            is_default: bool = False,
    ) -> int:
        now = time.time()
        with self._connect() as conn:
            # If setting as default, remove default flag from other presets
            if is_default:
                conn.execute("UPDATE video_presets SET is_default = 0 WHERE user_id = ?", (user_id,))
            cursor = conn.execute(
                """INSERT INTO video_presets
                   (user_id, name, template, transition, color_effect, audio_filters_json, video_quality, is_default,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, name, template, transition, color_effect,
                 json.dumps(audio_filters) if audio_filters else None, video_quality, 1 if is_default else 0, now, now),
            )
            return cursor.lastrowid

    def list_video_presets(self, user_id: int) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM video_presets WHERE user_id = ? ORDER BY is_default DESC, created_at DESC",
                (user_id,),
            ).fetchall()
            presets = []
            for row in rows:
                preset = dict(row)
                if preset["audio_filters_json"]:
                    preset["audio_filters"] = json.loads(preset["audio_filters_json"])
                del preset["audio_filters_json"]
                preset["is_default"] = bool(preset["is_default"])
                presets.append(preset)
            return presets

    def get_video_preset(self, preset_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM video_presets WHERE id = ? AND user_id = ?",
                (preset_id, user_id),
            ).fetchone()
            if row:
                preset = dict(row)
                if preset["audio_filters_json"]:
                    preset["audio_filters"] = json.loads(preset["audio_filters_json"])
                del preset["audio_filters_json"]
                preset["is_default"] = bool(preset["is_default"])
                return preset
            return None

    def update_video_preset(
            self,
            preset_id: int,
            user_id: int,
            name: Optional[str] = None,
            template: Optional[str] = None,
            transition: Optional[str] = None,
            color_effect: Optional[str] = None,
            audio_filters: Optional[Dict[str, Any]] = None,
            video_quality: Optional[str] = None,
            is_default: Optional[bool] = None,
    ) -> bool:
        now = time.time()
        with self._connect() as conn:
            # Check ownership
            existing = conn.execute(
                "SELECT id FROM video_presets WHERE id = ? AND user_id = ?",
                (preset_id, user_id),
            ).fetchone()
            if not existing:
                return False

            # Build update query
            updates = []
            params = []
            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if template is not None:
                updates.append("template = ?")
                params.append(template)
            if transition is not None:
                updates.append("transition = ?")
                params.append(transition)
            if color_effect is not None:
                updates.append("color_effect = ?")
                params.append(color_effect)
            if audio_filters is not None:
                updates.append("audio_filters_json = ?")
                params.append(json.dumps(audio_filters))
            if video_quality is not None:
                updates.append("video_quality = ?")
                params.append(video_quality)
            if is_default is not None:
                if is_default:
                    conn.execute("UPDATE video_presets SET is_default = 0 WHERE user_id = ?", (user_id,))
                updates.append("is_default = ?")
                params.append(1 if is_default else 0)

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.extend([preset_id, user_id])
                conn.execute(
                    f"UPDATE video_presets SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                    params,
                )
            return True

    def delete_video_preset(self, preset_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM video_presets WHERE id = ? AND user_id = ?",
                (preset_id, user_id),
            )
            return cursor.rowcount > 0

    # ---- admin / stats ----
    def admin_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            jobs_by_status = {
                row["status"]: row["c"]
                for row in conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status")
            }
            total_jobs = sum(jobs_by_status.values())
            publishes_by_platform = {
                row["platform"]: row["c"]
                for row in conn.execute(
                    "SELECT platform, COUNT(*) c FROM publish_log WHERE success = 1 GROUP BY platform"
                )
            }
            jobs_last_7d = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE created_at > ?", (time.time() - 7 * 86400,)
            ).fetchone()["c"]
            total_credits_issued = conn.execute("SELECT COALESCE(SUM(credits),0) s FROM users").fetchone()["s"]
            return {
                "total_users": total_users,
                "total_jobs": total_jobs,
                "jobs_by_status": jobs_by_status,
                "jobs_last_7d": jobs_last_7d,
                "publishes_by_platform": publishes_by_platform,
                "total_credits_outstanding": total_credits_issued,
            }
