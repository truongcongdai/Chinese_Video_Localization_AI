from pathlib import Path


STATIC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "universal_video_ai"
    / "web"
    / "static"
)


def test_youtube_research_workspace_contract() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for contract in (
        'data-feature="youtube-research"',
        'id="youtube-research-niche"',
        'id="youtube-research-keyword"',
        'id="youtube-research-country"',
        'id="youtube-research-language"',
        'id="youtube-research-max-results"',
        'id="youtube-research-scan-btn"',
        'id="youtube-research-loading"',
        'id="youtube-research-empty"',
        'id="youtube-research-error"',
        'id="youtube-research-feature-disabled"',
        'id="youtube-research-collector-unavailable"',
        'id="youtube-research-results"',
    ):
        assert contract in html


def test_youtube_research_javascript_uses_real_api_handoff() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'api("/api/youtube-research/status")' in script
    assert 'api("/api/youtube-research/projects"' in script
    assert '"/scan"' in script
    assert '"/localize"' in script
    assert "data-localize-research" in script
    assert "Localize this video" in script
    assert "Unavailable" in script
    assert "synthetic" not in script.lower()
    assert "placeholder video" not in script.lower()
