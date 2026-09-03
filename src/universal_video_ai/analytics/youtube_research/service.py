from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from universal_video_ai.database.youtube_research import (
        YouTubeResearchProject,
        YouTubeResearchRepository,
    )

from .collector import (
    YouTubeCollectorError,
    YouTubeResearchCollector,
    canonical_youtube_url,
)
from .competition_analyzer import CompetitionAnalyzer
from .opportunity_analyzer import OpportunityAnalyzer
from .schemas import ResearchVideo
from .trend_analyzer import TrendAnalyzer


class ResearchProjectNotFoundError(LookupError):
    pass


class YouTubeResearchService:
    """Coordinates bounded collection, persistence, analysis, and ranking."""

    def __init__(
        self,
        repository: YouTubeResearchRepository,
        collector: YouTubeResearchCollector,
        *,
        hard_max_results: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.collector = collector
        self.hard_max_results = max(1, int(hard_max_results))
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(str(value or "").split())

    def create_project(
        self,
        user_id: int,
        *,
        niche: str,
        keyword: str,
        target_language: str | None = None,
        target_country: str | None = None,
    ) -> YouTubeResearchProject:
        clean_niche = self._clean(niche)
        clean_keyword = self._clean(keyword)
        if not clean_niche and not clean_keyword:
            raise ValueError("niche or keyword must not be empty")
        if not clean_keyword:
            clean_keyword = clean_niche
        project_id = self.repository.create_project(
            user_id=user_id,
            niche=clean_niche,
            keyword=clean_keyword,
            target_language=self._clean(target_language or "") or None,
            target_country=self._clean(target_country or "") or None,
        )
        project = self.repository.get_project(project_id, user_id)
        if project is None:
            raise RuntimeError("created research project could not be retrieved")
        return project

    def get_project(self, project_id: int, user_id: int) -> YouTubeResearchProject:
        project = self.repository.get_project(project_id, user_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project not found")
        return project

    def list_projects(self, user_id: int) -> list[YouTubeResearchProject]:
        return self.repository.list_projects(user_id)

    async def scan(
        self, project_id: int, user_id: int, *, max_results: int
    ) -> dict[str, Any]:
        project = self.get_project(project_id, user_id)
        requested = int(max_results)
        if requested < 1 or requested > self.hard_max_results:
            raise ValueError(f"max_results must be between 1 and {self.hard_max_results}")

        query_parts = [part for part in (project.niche, project.keyword) if part]
        query = " ".join(dict.fromkeys(query_parts))
        source_id = self.repository.create_search_execution(
            project_id, user_id, query=query, max_results=requested
        )
        try:
            collected = await self.collector.search(query, requested)
        except YouTubeCollectorError as exc:
            self.repository.finish_search_execution(
                source_id, project_id, user_id,
                status="failed", result_count=0, error=str(exc),
            )
            raise
        except Exception as exc:
            self.repository.finish_search_execution(
                source_id, project_id, user_id,
                status="failed", result_count=0, error="collector failed",
            )
            raise YouTubeCollectorError("YouTube metadata collection failed") from exc

        observed_at = self._clock()
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        videos = self._normalize_collected(collected, query, requested, observed_at)

        for video in videos:
            self.repository.upsert_video(project_id, user_id, video)
            self.repository.save_snapshot(
                project_id, user_id, video.video_id, self._video_payload(video)
            )

        self.repository.clear_opportunities(project_id, user_id)
        analysis_payload: dict[str, Any] = {}
        if videos:
            trend = TrendAnalyzer().analyze(videos, now=observed_at)
            competition = CompetitionAnalyzer().analyze(videos, now=observed_at)
            overall = OpportunityAnalyzer().analyze(trend, competition)
            analysis_payload = {
                "trend": asdict(trend),
                "competition": asdict(competition),
                "opportunity": asdict(overall),
            }
            self.repository.save_analysis(
                project_id, user_id, "trend", asdict(trend),
                score=trend.trend_score, confidence_score=trend.confidence_score,
            )
            self.repository.save_analysis(
                project_id, user_id, "competition", asdict(competition),
                score=competition.competition_score,
                confidence_score=competition.confidence_score,
            )
            self.repository.save_analysis(
                project_id, user_id, "opportunity", asdict(overall),
                score=overall.adjusted_score,
                confidence_score=overall.confidence_score,
            )

            for video in videos:
                item_trend = TrendAnalyzer().analyze([video], now=observed_at)
                opportunity = OpportunityAnalyzer().analyze(item_trend, competition)
                payload = asdict(opportunity)
                payload["explanations"] = [
                    *payload["explanations"],
                    self._availability_explanation(video),
                ]
                display_confidence = min(
                    trend.confidence_score, self._metadata_coverage(video) * 100.0
                )
                self.repository.save_opportunity(
                    project_id, user_id,
                    source_id=source_id,
                    video_id=video.video_id,
                    title=video.title,
                    raw_score=opportunity.raw_score,
                    adjusted_score=opportunity.adjusted_score,
                    confidence_score=display_confidence,
                    payload=payload,
                )

        self.repository.finish_search_execution(
            source_id, project_id, user_id,
            status="completed", result_count=len(videos), error=None,
        )
        return {
            "project": asdict(self.get_project(project_id, user_id)),
            "query": query,
            "result_count": len(videos),
            "analysis": analysis_payload,
            "results": self.repository.get_ranked_results(
                project_id, user_id, limit=requested
            ),
        }

    def ranked_results(
        self, project_id: int, user_id: int
    ) -> list[dict[str, Any]]:
        self.get_project(project_id, user_id)
        return self.repository.get_ranked_results(
            project_id, user_id, limit=self.hard_max_results
        )

    @staticmethod
    def _normalize_collected(
        collected: list[ResearchVideo],
        query: str,
        max_results: int,
        observed_at: datetime,
    ) -> list[ResearchVideo]:
        output: list[ResearchVideo] = []
        seen: set[str] = set()
        for item in collected:
            video_id = str(item.video_id or "").strip()
            try:
                canonical_url = canonical_youtube_url(video_id)
            except ValueError:
                continue
            if video_id in seen or len(output) >= max_results:
                continue
            seen.add(video_id)
            output.append(
                replace(
                    item,
                    video_id=video_id,
                    canonical_url=canonical_url,
                    title=str(item.title or "").strip(),
                    search_query=query,
                    collected_at=observed_at,
                )
            )
        return output

    @staticmethod
    def _video_payload(video: ResearchVideo) -> dict[str, Any]:
        payload = asdict(video)
        payload["published_at"] = (
            video.published_at.isoformat() if video.published_at else None
        )
        payload["collected_at"] = video.collected_at.isoformat()
        return payload

    @staticmethod
    def _metadata_coverage(video: ResearchVideo) -> float:
        metrics = (
            video.published_at,
            video.duration_seconds,
            video.view_count,
            video.like_count,
            video.comment_count,
            video.subscriber_count,
        )
        return sum(value is not None for value in metrics) / len(metrics)

    @classmethod
    def _availability_explanation(cls, video: ResearchVideo) -> str:
        available = round(cls._metadata_coverage(video) * 6)
        return f"{available} of 6 optional date/duration/engagement metrics were available."
