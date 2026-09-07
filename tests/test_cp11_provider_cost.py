from __future__ import annotations

import pytest

from universal_video_ai.provider_runtime import (
    LiveProviderDisabled,
    ProviderBudget,
    ProviderBudgetExceeded,
    ProviderMode,
    cache_key,
    execute_provider_call,
    get_cost_report,
    reset_cost_report,
)


@pytest.fixture(autouse=True)
def isolated_provider_report(monkeypatch):
    monkeypatch.delenv("RUN_LIVE_TESTS", raising=False)
    reset_cost_report(clear_cache=True)
    yield
    reset_cost_report(clear_cache=True)


def test_mock_mode_is_deterministic_and_never_calls_live():
    calls = {"live": 0}
    result = execute_provider_call(
        "ollama", "analysis", {"topic": "safe"}, mode=ProviderMode.MOCK,
        live=lambda: calls.__setitem__("live", calls["live"] + 1),
        mock=lambda: {"answer": "fixture"}, category="llm",
    )
    report = get_cost_report()
    assert result == {"answer": "fixture"}
    assert calls["live"] == 0 and report.mock_calls == 1 and report.live_calls == 0
    assert report.llm_calls == 1


def test_cache_miss_uses_mock_without_live_then_hits_cache():
    calls = {"mock": 0, "live": 0}
    def mock():
        calls["mock"] += 1
        return {"stable": 7}
    kwargs = dict(
        provider="translation", operation="translate", payload={"text": "hello"},
        mode="CACHE", live=lambda: calls.__setitem__("live", calls["live"] + 1),
        mock=mock, cacheable=True,
    )
    assert execute_provider_call(**kwargs) == {"stable": 7}
    assert execute_provider_call(**kwargs) == {"stable": 7}
    report = get_cost_report()
    assert calls == {"mock": 1, "live": 0}
    assert report.cache_misses == 1 and report.cache_hits == 1 and report.live_calls == 0


def test_cache_key_strips_tokens_and_is_input_sensitive():
    first = cache_key("youtube", "videos", {"video": "abc123", "access_token": "one", "client_secret": "a"})
    second = cache_key("youtube", "videos", {"video": "abc123", "access_token": "two", "client_secret": "b"})
    third = cache_key("youtube", "videos", {"video": "different", "access_token": "one"})
    assert first == second
    assert first != third


def test_oauth_responses_are_never_cacheable():
    with pytest.raises(ValueError, match="cannot be provider-cached"):
        execute_provider_call(
            "google", "oauth_token_exchange", {}, mode="CACHE", cacheable=True,
            live=lambda: {}, mock=lambda: {},
        )


def test_live_requires_explicit_opt_in(monkeypatch):
    with pytest.raises(LiveProviderDisabled, match="RUN_LIVE_TESTS=1"):
        execute_provider_call(
            "youtube", "analytics", {}, mode="LIVE", live=lambda: {"views": 1}
        )
    assert get_cost_report().live_calls == 0


def test_live_opt_in_is_counted_but_test_restores_zero(monkeypatch):
    monkeypatch.setenv("RUN_LIVE_TESTS", "1")
    result = execute_provider_call(
        "fake-live", "acceptance", {}, mode="LIVE", live=lambda: {"ok": True}
    )
    assert result["ok"] and get_cost_report().live_calls == 1
    # This is an in-process fake proving the opt-in boundary, not a provider
    # acceptance. Restore the session report so normal pytest remains zero-live.
    reset_cost_report(clear_cache=True)


def test_budget_exceeded_stops_before_an_extra_call():
    budget = ProviderBudget(max_external_api_calls=1)
    execute_provider_call(
        "research", "scan", {"q": "one"}, mode="MOCK",
        live=lambda: None, mock=lambda: {"items": []}, budget=budget,
    )
    with pytest.raises(ProviderBudgetExceeded):
        execute_provider_call(
            "research", "scan", {"q": "two"}, mode="MOCK",
            live=lambda: None, mock=lambda: {"items": []}, budget=budget,
        )
    assert get_cost_report().provider_calls == 1


def test_voice_catalog_does_not_synthesize_outside_live_mode(monkeypatch):
    import importlib

    web_app = importlib.import_module("universal_video_ai.web.app")
    observed_verify = []

    class FakeRegistry:
        def list_voices(self, language, *, provider=None, refresh=False, verify=False):
            observed_verify.append(verify)
            return []

        def health(self):
            return []

        def distinct_speaker_count(self, voices):
            return 0

    registry = FakeRegistry()
    monkeypatch.setattr(web_app, "get_default_voice_registry", lambda: registry)
    monkeypatch.setattr(web_app, "default_provider_mode", lambda: ProviderMode.MOCK)

    result = web_app.list_voices(
        language="vi", provider="edge", refresh=True, user_id=1
    )
    assert result["voices"] == []
    assert observed_verify == [False]
