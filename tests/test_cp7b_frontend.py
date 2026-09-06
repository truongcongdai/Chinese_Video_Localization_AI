from pathlib import Path


def test_production_queue_exposes_cp7b_render_progress_and_actions():
    root = Path(__file__).parents[1]
    app = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    for marker in (
        "production-render-workspace",
        "production-submit-render",
        "data-production-render-job",
        "source_media_rights",
        "speaker_mapping",
        "source_subtitle_boxes",
        "/render-jobs",
    ):
        assert marker in app
    assert "CP7B executes approved assets" in html
    assert "user_id:" not in app[app.index("production-submit-render"):app.index("production-submit-render") + 3000]


def test_voice_catalog_shows_provider_availability_and_refresh():
    root = Path(__file__).parents[1]
    app = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    assert 'id="voice-refresh-btn"' in html
    assert "provider === \"edge\" ? \"free\" : provider" in app
    assert "voice.cost_class" in app
    assert "voice.is_local" in app


def test_production_queue_exposes_cp8_manual_publishing_controls():
    root = Path(__file__).parents[1]
    app = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    for marker in (
        "CP8 Publishing &amp; Scheduling", "production-publish-dry-run",
        "production-publish-now", "production-publish-schedule-action",
        "Confirm PUBLIC publishing", "/publishing-jobs",
    ):
        assert marker in app
    assert "user_id:" not in app[app.index("production-publish-dry-run"):]
