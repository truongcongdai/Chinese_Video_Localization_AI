from types import SimpleNamespace
import asyncio

from universal_video_ai.web import app as web_app


def test_preflight_allows_contextual_translation_with_running_ollama(monkeypatch):
    monkeypatch.setattr(web_app.store, "get_provider_settings", lambda user_id, provider: None)
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 999})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "qwen3:8b"}]}

    monkeypatch.setattr(web_app.requests, "get", lambda url, timeout: FakeResponse())

    report = web_app._job_preflight_report(
        1,
        web_app.NewJobBody(
            url="https://example.com/video.mp4",
            translation_mode="contextual",
            translation_model="qwen3:8b",
            tts_provider="edge",
        ),
        url_count=1,
    )

    assert report["ok"] is True
    assert "missing_llm_connection" not in {issue["code"] for issue in report["issues"]}
    assert "contextual_translation_ollama" in {issue["code"] for issue in report["issues"]}


def test_preflight_warns_when_contextual_translation_ollama_is_down(monkeypatch):
    monkeypatch.setattr(web_app.store, "get_provider_settings", lambda user_id, provider: None)
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 999})

    def fake_get(url, timeout):
        raise web_app.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(web_app.requests, "get", fake_get)

    report = web_app._job_preflight_report(
        1,
        web_app.NewJobBody(
            url="https://example.com/video.mp4",
            translation_mode="contextual",
            translation_model="qwen3:8b",
            tts_provider="edge",
        ),
        url_count=1,
    )

    assert report["ok"] is True
    assert "ollama_unavailable" in {issue["code"] for issue in report["issues"]}
    issue = next(issue for issue in report["issues"] if issue["code"] == "ollama_unavailable")
    assert issue["severity"] == "warning"


def test_preflight_surfaces_remix_plan_for_long_form(monkeypatch):
    monkeypatch.setattr(web_app.store, "get_provider_settings", lambda user_id, provider: None)
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 999})
    monkeypatch.setattr(
        web_app.requests,
        "get",
        lambda url, timeout: (_ for _ in ()).throw(web_app.requests.exceptions.ConnectionError("refused")),
    )

    report = web_app._job_preflight_report(
        1,
        web_app.NewJobBody(
            url="https://example.com/video.mp4",
            remix_enabled=True,
            remix_platforms=["youtube_long", "facebook_long", "youtube_shorts"],
            remix_goal="education",
            remix_strength="strong",
            processing_mode="fast",
            translation_mode="contextual",
            tts_provider="edge",
        ),
        url_count=1,
    )

    codes = {issue["code"] for issue in report["issues"]}
    assert report["ok"] is True
    assert "remix_plan" in codes
    assert "long_form_quality" in codes


def test_create_job_remix_applies_visible_transform_defaults(monkeypatch):
    captured = {}
    configured_limits = []

    def fake_create_job(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="job123", to_dict=lambda: {"id": "job123"})

    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace(cancel=lambda: None)

    async def fake_configure_job_run_limit(value):
        configured_limits.append(value)
        return value

    monkeypatch.setattr(web_app.store, "get_provider_settings", lambda user_id, provider: None)
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 999})
    monkeypatch.setattr(web_app.store, "create_job", fake_create_job)
    monkeypatch.setattr(web_app.store, "adjust_credits", lambda user_id, delta: 998)
    monkeypatch.setattr(web_app, "_require_license_or_trial", lambda user_id: "trial")
    monkeypatch.setattr(web_app.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(web_app, "_configure_job_run_limit", fake_configure_job_run_limit)
    monkeypatch.setattr(
        web_app.requests,
        "get",
        lambda url, timeout: (_ for _ in ()).throw(web_app.requests.exceptions.ConnectionError("refused")),
    )

    result = asyncio.run(web_app.create_job(
        web_app.NewJobBody(
            url="https://example.com/video.mp4",
            remix_enabled=True,
            remix_platforms=["tiktok"],
            remix_strength="strong",
            tts_provider="edge",
            max_concurrent=4,
        ),
        user_id=1,
    ))

    assert result == {"id": "job123"}
    assert captured["remix_enabled"] is True
    assert captured["translation_mode"] == "contextual"
    assert captured["video_template_config"]["color_effect"] == "high_contrast"
    assert captured["transform_config"]["enable_randomization"] is True
    assert captured["transform_config"]["crop_percent"] == 1.2
    assert captured["transform_config"]["speed_factor"] == 1.0
    assert configured_limits == [4]


def test_preflight_blocks_gemini_mode_without_saved_key(monkeypatch):
    monkeypatch.setattr(web_app.store, "get_provider_settings", lambda user_id, provider: None)
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 999})

    report = web_app._job_preflight_report(
        1,
        web_app.NewJobBody(
            url="https://example.com/video.mp4",
            translation_mode="gemini",
            tts_provider="edge",
        ),
        url_count=1,
    )

    assert report["ok"] is False
    assert "missing_gemini_connection" in {issue["code"] for issue in report["issues"]}


def test_preflight_uses_saved_gemini_connection_for_gemini_mode(monkeypatch):
    def fake_provider_settings(user_id, provider):
        if provider == "gemini":
            return {
                "api_key": "test-key",
                "default_model": "gemini-3.1-flash-lite",
                "extra": {"llm_models": ["gemini-3.1-flash-lite"]},
            }
        return None

    monkeypatch.setattr(web_app.store, "get_provider_settings", fake_provider_settings)
    monkeypatch.setattr(web_app.store, "get_user_by_id", lambda user_id: {"credits": 999})

    report = web_app._job_preflight_report(
        1,
        web_app.NewJobBody(
            url="https://example.com/video.mp4",
            translation_mode="gemini",
            translation_model="gemini-3.1-flash-lite",
            tts_provider="edge",
        ),
        url_count=1,
    )

    assert report["ok"] is True
    assert "contextual_translation_gemini" in {issue["code"] for issue in report["issues"]}


def test_probe_provider_gemini_lists_generate_content_models(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "models": [
                    {
                        "name": "models/gemini-3.1-flash-lite",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }

    monkeypatch.setattr(web_app.requests, "get", lambda url, headers, params, timeout: FakeResponse())

    result = web_app._probe_provider("gemini", "test-key")

    assert result["models"] == ["gemini-3.1-flash-lite"]
    assert result["llm_models"] == ["gemini-3.1-flash-lite"]


def test_build_service_uses_strict_gemini_adapter(monkeypatch):
    def fake_provider_settings(user_id, provider):
        if provider == "gemini":
            return {
                "api_key": "test-key",
                "default_model": "gemini-3.1-flash-lite",
                "extra": {"llm_models": ["gemini-3.1-flash-lite"]},
            }
        return None

    monkeypatch.setattr(web_app.store, "get_provider_settings", fake_provider_settings)
    job = SimpleNamespace(
        user_id=1,
        logo_path=None,
        logo_corner=None,
        logo_size_px=None,
        animated_subtitle_config=None,
        video_template_config=None,
        transform_config=None,
        processing_mode="quality",
        source_language="auto",
        target_language="vi",
        tts_provider="edge",
        tts_voice=None,
        tts_style="natural",
        tts_model=None,
        translation_mode="gemini",
        translation_model="gemini-3.1-flash-lite",
        translation_tone="natural",
        translation_audience=None,
        translation_glossary=None,
        subtitle_offset_seconds=0.0,
    )

    service = web_app._build_service_for_job(job)

    assert service.segment_adapter.config.provider == "gemini"
    assert service.segment_adapter.config.model == "gemini-3.1-flash-lite"
    assert service.segment_adapter.config.fallback_on_error is False
    assert service.config.global_subtitle_offset == 0.0
