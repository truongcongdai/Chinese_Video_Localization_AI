from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from universal_video_ai import config
from universal_video_ai.analytics.youtube_research import ResearchVideo
from universal_video_ai.database import DatabaseManager, YouTubeResearchRepository
from universal_video_ai.downloader.channel import URLIntent
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.web import app as web_app
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.store import Store


class OneVideoCollector:
    async def search(self, _query, _max_results):
        return [ResearchVideo(video_id="abc123def45", title="Selected candidate")]


def test_research_candidate_uses_existing_preflight_and_normal_owned_job(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "web.sqlite3"
    test_store = Store(db_path)
    user_id = test_store.create_user("research-user", "unused", credits=10)
    manager = DatabaseManager(db_path)
    manager.init_schema()
    repository = YouTubeResearchRepository(manager)

    monkeypatch.setattr(config, "YOUTUBE_RESEARCH_ENABLED", True)
    monkeypatch.setattr(web_app, "store", test_store)
    monkeypatch.setattr(web_app, "JOB_COST_CREDITS", 0)
    monkeypatch.setattr(
        web_app.app.state, "youtube_research_repository", repository
    )
    monkeypatch.setattr(
        web_app.app.state, "youtube_research_collector", OneVideoCollector()
    )

    async def no_pipeline_run(_job_id):
        return None

    monkeypatch.setattr(web_app, "_run_job", no_pipeline_run)
    monkeypatch.setattr(
        web_app._video_url_classifier,
        "classify",
        lambda url: SimpleNamespace(
            platform=Platform.YOUTUBE,
            intent=URLIntent.VIDEO,
            resolved_url=url,
        ),
    )
    preflight_calls = []
    original_preflight = web_app._job_preflight_report

    def tracked_preflight(owner_id, body, *, url_count):
        preflight_calls.append((owner_id, body.url, url_count))
        return original_preflight(owner_id, body, url_count=url_count)

    monkeypatch.setattr(web_app, "_job_preflight_report", tracked_preflight)
    web_app.app.dependency_overrides[get_current_user_id] = lambda: user_id
    try:
        with TestClient(web_app.app) as client:
            project_response = client.post(
                "/api/youtube-research/projects",
                json={"niche": "automation", "keyword": "python"},
            )
            assert project_response.status_code == 200
            project_id = project_response.json()["id"]
            assert client.post(
                f"/api/youtube-research/projects/{project_id}/scan",
                json={"max_results": 1},
            ).status_code == 200

            localized = client.post(
                f"/api/youtube-research/projects/{project_id}/localize",
                json={"video_id": "abc123def45", "target_language": "vi"},
            )
            assert localized.status_code == 200
            assert localized.json()["status"] == "queued"
    finally:
        web_app.app.dependency_overrides.pop(get_current_user_id, None)

    canonical_url = "https://www.youtube.com/watch?v=abc123def45"
    assert preflight_calls == [(user_id, canonical_url, 1)]
    jobs = test_store.list_jobs_for_user(user_id)
    assert len(jobs) == 1
    assert jobs[0].source_url == canonical_url
    assert jobs[0].target_language == "vi"
    assert jobs[0].status == "queued"
    assert test_store.list_jobs_for_user(user_id + 1) == []
