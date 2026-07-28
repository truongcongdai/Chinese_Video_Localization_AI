from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "universal_video_ai" / "web" / "static"


def _read_static_file(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_reup_voice_ui_has_single_voice_selector() -> None:
    html = _read_static_file("index.html")
    app_js = _read_static_file("app.js")

    assert 'id="voice-select"' in html
    assert 'id="provider-voice-select"' not in html
    assert 'id="voice-library"' not in html
    assert "provider-voice-select" not in app_js
    assert "voice-library-card" in app_js  # renderer remains safe for optional future containers


def test_provider_connect_buttons_do_not_directly_redirect_outside_panel() -> None:
    html = _read_static_file("index.html")
    app_js = _read_static_file("app.js")

    assert 'id="translation-connect-link" href="#"' in html
    assert 'id="provider-connect-link" href="#"' in html
    assert 'id="provider-open-dashboard-link"' in html
    assert '$("#translation-connect-link").href = "#";' in app_js
    assert 'connectLink.href = "#";' in app_js
    assert 'openProviderConnect("openai", false)' in app_js


def test_recommended_defaults_use_tiktok_karaoke_subtitles() -> None:
    html = _read_static_file("index.html")
    app_js = _read_static_file("app.js")

    assert 'data-platform-preset="tiktok"' in html
    assert 'platform-preset-card active' in html
    assert 'data-subtitle-template="karaoke"' in html
    assert 'subtitle-template-card active' in html
    assert 'id="animated-subtitle-checkbox" checked' in html
    assert '<option value="karaoke" selected>' in html
    assert 'function applyRecommendedLocalizationDefaults()' in app_js
    assert '$("#subtitle-effect-select").value = "karaoke";' in app_js
    assert '$("#output-aspect-ratio").value = "9:16";' in app_js


def test_youtube_16_9_preset_and_preflight_are_available() -> None:
    html = _read_static_file("index.html")
    app_js = _read_static_file("app.js")

    assert 'data-platform-preset="youtube"' in html
    assert "YouTube 16:9" in html
    assert '<option value="16:9">Ngang 16:9 — 1920×1080</option>' in html
    assert 'id="preflight-panel"' in html
    assert "youtube: { aspect: \"16:9\"" in app_js
    assert 'target_aspect_ratio: $("#output-aspect-ratio").value' in app_js
    assert 'api("/api/jobs/preflight"' in app_js
