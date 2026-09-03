from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_video_ai import config
from universal_video_ai.analytics.youtube_research import (
    ResearchVideo,
    YouTubeCollectorError,
    YouTubeCollectorUnavailableError,
)
from universal_video_ai.database import DatabaseManager, YouTubeResearchRepository
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.youtube_research import router


class FakeYouTubeCollector:
    def __init__(self, videos=None, error=None):
        self.videos = list(videos or [])
        self.error = error
        self.calls = []

    async def search(self, query, max_results):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.videos


@pytest.fixture
def research_api(tmp_path, monkeypatch):
    manager = DatabaseManager(tmp_path / "api.sqlite3")
    manager.init_schema()
    repository = YouTubeResearchRepository(manager)
    collector = FakeYouTubeCollector(
        [
            ResearchVideo(
                video_id="abc123def45",
                title="Real fake-fixture metadata",
                view_count=1200,
                collected_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            )
        ]
    )
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.state.youtube_research_repository = repository
    test_app.state.youtube_research_collector = collector
    submitted = []

    async def submit(url, language, user_id):
        submitted.append((url, language, user_id))
        return {
            "id": "normal-job-id",
            "user_id": user_id,
            "source_url": url,
            "target_language": language,
            "status": "queued",
        }

    test_app.state.youtube_research_submit_localization = submit
    current_user = {"id": 101}
    test_app.dependency_overrides[get_current_user_id] = lambda: current_user["id"]
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_ENABLED", True)
    with TestClient(test_app) as client:
        yield client, repository, collector, current_user, submitted


def _create_project(client):
    response = client.post(
        "/api/youtube-research/projects",
        json={
            "niche": "automation",
            "keyword": "python",
            "target_language": "vi",
            "target_country": "VN",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_router_is_mounted_in_main_openapi() -> None:
    from universal_video_ai.web.app import app

    paths = app.openapi()["paths"]
    assert "/api/youtube-research/projects" in paths
    assert "/api/youtube-research/projects/{project_id}/scan" in paths
    assert "/api/youtube-research/projects/{project_id}/localize" in paths


def test_unauthenticated_research_access_is_rejected(tmp_path) -> None:
    test_app = FastAPI()
    test_app.include_router(router)
    with TestClient(test_app) as client:
        response = client.get("/api/youtube-research/status")
    assert response.status_code == 401


def test_status_create_list_scan_results_and_localize(research_api) -> None:
    client, _, collector, _, submitted = research_api
    status = client.get("/api/youtube-research/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["database_enabled"] is True

    project = _create_project(client)
    listed = client.get("/api/youtube-research/projects")
    assert [item["id"] for item in listed.json()] == [project["id"]]

    scan = client.post(
        f"/api/youtube-research/projects/{project['id']}/scan",
        json={"max_results": 5},
    )
    assert scan.status_code == 200
    assert scan.json()["result_count"] == 1
    assert collector.calls == [("automation python", 5)]

    results = client.get(
        f"/api/youtube-research/projects/{project['id']}/results"
    )
    assert results.status_code == 200
    assert results.json()["results"][0]["video_id"] == "abc123def45"

    localized = client.post(
        f"/api/youtube-research/projects/{project['id']}/localize",
        json={"video_id": "abc123def45", "target_language": "vi"},
    )
    assert localized.status_code == 200
    assert localized.json()["status"] == "queued"
    assert submitted == [
        ("https://www.youtube.com/watch?v=abc123def45", "vi", 101)
    ]


def test_tenant_isolation_for_read_scan_results_and_localize(research_api) -> None:
    client, _, collector, current_user, _ = research_api
    project = _create_project(client)
    client.post(
        f"/api/youtube-research/projects/{project['id']}/scan",
        json={"max_results": 2},
    )
    call_count = len(collector.calls)
    current_user["id"] = 202

    assert client.get(
        f"/api/youtube-research/projects/{project['id']}"
    ).status_code == 404
    assert client.post(
        f"/api/youtube-research/projects/{project['id']}/scan",
        json={"max_results": 2},
    ).status_code == 404
    assert client.get(
        f"/api/youtube-research/projects/{project['id']}/results"
    ).status_code == 404
    assert client.post(
        f"/api/youtube-research/projects/{project['id']}/localize",
        json={"video_id": "abc123def45", "target_language": "vi"},
    ).status_code == 404
    assert len(collector.calls) == call_count


def test_feature_disabled_and_bounded_scan_are_explicit(research_api, monkeypatch) -> None:
    client, _, _, _, _ = research_api
    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_ENABLED", False)
    assert client.get("/api/youtube-research/status").json()["enabled"] is False
    disabled = client.post(
        "/api/youtube-research/projects", json={"niche": "x", "keyword": "y"}
    )
    assert disabled.status_code == 403
    assert disabled.json()["detail"]["code"] == "feature_disabled"

    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_ENABLED", True)
    project = _create_project(client)
    invalid = client.post(
        f"/api/youtube-research/projects/{project['id']}/scan",
        json={"max_results": config.YOUTUBE_RESEARCH_MAX_RESULTS + 1},
    )
    assert invalid.status_code == 422


def test_collector_failure_is_truthful_and_returns_no_candidates(
    research_api,
) -> None:
    client, _, collector, _, _ = research_api
    project = _create_project(client)
    collector.error = YouTubeCollectorError("YouTube is unavailable")
    response = client.post(
        f"/api/youtube-research/projects/{project['id']}/scan",
        json={"max_results": 2},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "collector_failed", "message": "YouTube is unavailable"
    }
    results = client.get(
        f"/api/youtube-research/projects/{project['id']}/results"
    )
    assert results.json()["results"] == []


def test_collector_unavailable_has_distinct_error(research_api) -> None:
    client, _, collector, _, _ = research_api
    project = _create_project(client)
    collector.error = YouTubeCollectorUnavailableError("yt-dlp is not installed")
    response = client.post(
        f"/api/youtube-research/projects/{project['id']}/scan",
        json={"max_results": 2},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "collector_unavailable"


def test_authenticated_opportunity_analysis(research_api) -> None:
    client, *_ = research_api
    response = client.post(
        "/api/youtube-research/analyze/opportunity",
        json={
            "videos": [
                {"video_id": "v1", "title": "One", "view_count": 100},
                {"video_id": "v2", "title": "Two", "view_count": None},
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["opportunity"]["metadata"]["score_is_prediction"] is False
