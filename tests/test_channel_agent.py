from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from universal_video_ai import config
from universal_video_ai.channel_agent.analytics import (
    engagement_rate,
    outlier_ratio,
    trend_score,
    view_velocity,
)
from universal_video_ai.channel_agent.models import RightsStatus, SourceMetadata
from universal_video_ai.channel_agent.providers import ProviderStatus
from universal_video_ai.web import channel_agent_router as channel_agent_router_module
from universal_video_ai.web.channel_agent_router import channel_agent_status, router


class _NoOAuthStore:
    def get_social_account(self, user_id: int, platform: str):
        return None


def test_channel_agent_flag_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_CHANNEL_AGENT_ENABLED", raising=False)

    assert config.is_ai_channel_agent_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_channel_agent_flag_reads_enabled_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", value)

    assert config.is_ai_channel_agent_enabled() is True


def test_view_velocity_calculates_views_per_hour() -> None:
    first = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)

    assert view_velocity(1000, 2200, first, first + timedelta(hours=2)) == 600.0


def test_view_velocity_handles_invalid_deltas_safely() -> None:
    first = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)

    assert view_velocity(1000, 2200, first, first) == 0.0
    assert view_velocity(1000, 2200, first, first - timedelta(hours=1)) == 0.0
    assert view_velocity(2200, 1000, first, first + timedelta(hours=1)) == 0.0
    assert view_velocity(None, None, first, first + timedelta(hours=1)) == 0.0


def test_view_velocity_requires_timezone_aware_timestamps() -> None:
    naive = datetime(2026, 8, 20, 10, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        view_velocity(1000, 2200, naive, naive + timedelta(hours=2))


def test_engagement_rate_handles_zero_and_missing_values() -> None:
    assert engagement_rate(views=0, likes=100, comments=10) == 0.0
    assert engagement_rate(views=None, likes=None, comments=None) == 0.0
    assert engagement_rate(views=1000, likes=None, comments=50) == 0.05
    assert engagement_rate(views=1000, likes=-5, comments=10) == 0.01


def test_outlier_ratio_handles_zero_typical_views_and_optional_cap() -> None:
    assert outlier_ratio(500_000, 20_000) == 25.0
    assert outlier_ratio(500_000, 0) == 0.0
    assert outlier_ratio(None, None) == 0.0
    assert outlier_ratio(500_000, 20_000, cap=10.0) == 10.0


def test_trend_score_is_finite_and_bounded() -> None:
    assert trend_score() == 0.0
    assert trend_score(
        velocity_score=2.0,
        outlier_score=float("inf"),
        engagement_score=float("nan"),
        freshness_score=-10.0,
        competition_score=5.0,
    ) == pytest.approx(0.40)
    assert 0.0 <= trend_score(
        velocity_score=1,
        outlier_score=1,
        engagement_score=1,
        freshness_score=1,
        competition_score=1,
    ) <= 1.0


def test_source_metadata_defaults_rights_to_unknown() -> None:
    source = SourceMetadata(
        platform="youtube",
        source_id="video-1",
        source_url="https://www.youtube.com/watch?v=video-1",
        title="Research item",
        captured_at=datetime.now(timezone.utc),
    )

    assert source.rights_status is RightsStatus.UNKNOWN


def test_channel_agent_status_api_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")

    response = channel_agent_status(user_id=1, store=_NoOAuthStore())

    assert response.model_dump() == {
        "enabled": False,
        "version": "mvp",
        "youtube_connected": False,
        "youtube_credential_present": False,
        "youtube_connection_verified": None,
        "ollama_available": None,
        "ollama": None,
    }


def test_channel_agent_status_api_when_enabled_reports_real_ollama_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    class FakeOllama:
        def status(self) -> ProviderStatus:
            return ProviderStatus(True, True, "local-model", True, "ready")
    monkeypatch.setattr(channel_agent_router_module, "_brain_provider", lambda: FakeOllama())

    response = channel_agent_status(user_id=1, store=_NoOAuthStore())

    assert response.model_dump() == {
        "enabled": True,
        "version": "mvp",
        "youtube_connected": False,
        "youtube_credential_present": False,
        "youtube_connection_verified": None,
        "ollama_available": True,
        "ollama": {
            "enabled": True,
            "reachable": True,
            "configured_model": "local-model",
            "model_available": True,
            "message": "ready",
        },
    }
    assert "/api/channel-agent/status" in {route.path for route in router.routes}


def test_channel_agent_ui_is_hidden_until_bootstrap_enables_it() -> None:
    static_dir = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "universal_video_ai"
        / "web"
        / "static"
    )
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    javascript = (static_dir / "app.js").read_text(encoding="utf-8")
    brain_ui = (static_dir / "content_brain_ui.js").read_text(encoding="utf-8")

    assert 'class="feature-tab hidden" data-feature="channel-agent"' in html
    assert 'data-feature="channel-agent"' in html
    assert "AI Channel Agent" in html
    assert "boot.features.ai_channel_agent" in javascript
    assert 'api("/api/channel-agent/youtube/status")' in javascript
    assert 'id="channel-agent-connect-btn"' in html
    assert "channel-agent-cp4" in html
    assert 'id="content-brain-analyze"' in html
    assert 'api("/api/channel-agent/brain/status")' in javascript
    assert html.index("content_brain_ui.js") < html.index("app.js")
    assert 'mode = ContentBrainUI.normalizeMode($("#content-brain-mode").value)' in javascript
    assert "request_type: normalizeMode(mode)" in brain_ui
    assert 'data-content-brain-state="loading"' in javascript
    assert 'data-content-brain-state="error"' in javascript
    assert "contentBrainRequestState.isCurrent(token)" in javascript
    assert "ContentBrainUI.modeView(result, storedRequestType)" in javascript
    assert "result?.angles || result?.recommended_angles" in javascript
    assert "result?.titles || result?.recommended_titles" in javascript
    assert "result?.runtime_allocation" in javascript
    assert "run.generation_attempt_count" in javascript
    assert "run.failure_stage" in javascript
    history_handler = javascript[
        javascript.index('document.querySelectorAll("[data-content-brain-run]")'):
        javascript.index('$("#content-brain-status-refresh")')
    ]
    assert 'api(`/api/channel-agent/brain/runs/${runId}`)' in history_handler
    assert 'api("/api/channel-agent/brain/analyze"' not in history_handler
