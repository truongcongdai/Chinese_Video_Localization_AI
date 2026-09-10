"""Authenticated FastAPI adapter for Channel Agent own-channel reads."""

from __future__ import annotations

from datetime import date
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from universal_video_ai import config
from universal_video_ai.channel_agent.service import ChannelAgentService
from universal_video_ai.channel_agent.brain import (
    ContentBrainAlreadyRunning,
    ContentBrainError,
    ContentBrainInvalidResponse,
    ContentBrainService,
    EvidenceSelectionError,
    REQUEST_TYPES,
    SELECTOR_TYPES,
)
from universal_video_ai.channel_agent.providers import (
    OllamaProvider,
    OllamaProviderError,
    OllamaTimeoutError,
)
from universal_video_ai.channel_agent.opportunities import (
    COMPETITION_LEVELS,
    CONFIDENCE_LEVELS,
    SOURCE_TYPES,
    STATUSES,
    ContentOpportunityService,
    OpportunityError,
    OpportunityNotFound,
)
from universal_video_ai.channel_agent.production import (
    ACTIVE_ITEM_STATUSES,
    BLOCKER_REASONS,
    ITEM_STATUSES,
    RIGHTS_GATES,
    TASK_STATUSES,
    ProductionError,
    ProductionNotFound,
    ProductionQueueService,
)
from universal_video_ai.channel_agent.production_assets import (
    ASSET_TYPES,
    ProductionAssetError,
    ProductionAssetNotFound,
    ProductionAssetService,
)
from universal_video_ai.channel_agent.production_render import (
    ProductionRenderError,
    ProductionRenderNotFound,
    ProductionRenderService,
)
from universal_video_ai.channel_agent.production_publishing import (
    ProductionPublishingError,
    ProductionPublishingNotFound,
    ProductionPublishingService,
)
from universal_video_ai.channel_agent.feedback_learning import (
    FeedbackLearningError,
    FeedbackLearningNotFound,
    FeedbackLearningService,
)
from universal_video_ai.channel_agent.facebook_publishing import (
    FacebookPublishingError,
    FacebookPublishingNotFound,
    FacebookPublishingService,
)
from universal_video_ai.channel_agent.automation import (
    AutomationError,
    AutomationNotFound,
    AutomationOrchestrator,
    AutomationWaiting,
)
from universal_video_ai.channel_agent.autonomous_operator import (
    AutonomousChannelOperator,
    AutonomousOperatorError,
    AutonomousOperatorNotFound,
)
from universal_video_ai.provider_runtime import get_cost_report
from universal_video_ai.channel_agent.youtube import (
    GoogleOAuthTokenService,
    YouTubeReadOnlyError,
    YouTubeReadOnlyService,
    default_date_range,
)
from universal_video_ai.channel_agent.trends import (
    MAX_QUERIES_PER_SCAN,
    MAX_RESULTS_PER_QUERY,
    MAX_ENRICHMENT_CHANNELS,
    TrendScanAlreadyRunning,
    TrendScanError,
    YouTubeTrendScanner,
    YouTubeTrendSearchProvider,
    trend_min_relevance,
)
from universal_video_ai.channel_agent.competitors import (
    MAX_COMPETITORS,
    RECENT_VIDEOS,
    CompetitorError,
    CompetitorIntelligenceService,
    CompetitorRefreshRunning,
    YouTubeCompetitorProvider,
    opportunity_gaps,
)
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.store import Store


router = APIRouter(prefix="/api/channel-agent", tags=["channel-agent"])


class ChannelAgentStatusResponse(BaseModel):
    enabled: bool
    version: str
    youtube_connected: bool
    youtube_credential_present: bool
    youtube_connection_verified: Optional[bool]
    ollama_available: Optional[bool]
    ollama: Optional[dict[str, Any]] = None


class TrendQueryBody(BaseModel):
    query: str
    relevance_language: Optional[str] = None
    region_code: Optional[str] = None
    published_within_days: int = 30
    duration_filter: str = "long"
    search_order: str = "date"
    enabled: bool = True
    topic_terms: Optional[str] = None
    exclusion_terms: Optional[str] = None
    notes: Optional[str] = None


class TrendQueryUpdateBody(BaseModel):
    query: Optional[str] = None
    relevance_language: Optional[str] = None
    region_code: Optional[str] = None
    published_within_days: Optional[int] = None
    duration_filter: Optional[str] = None
    search_order: Optional[str] = None
    enabled: Optional[bool] = None
    topic_terms: Optional[str] = None
    exclusion_terms: Optional[str] = None
    notes: Optional[str] = None


class CompetitorAddBody(BaseModel):
    reference: str
    notes: Optional[str] = None


class CompetitorRefreshBody(BaseModel):
    competitor_id: Optional[int] = None
    mode: str = "long"


class ContentBrainAnalyzeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: str
    selector_type: str = "top_opportunity"
    selector_id: Optional[str] = None
    allow_low_confidence: bool = False


class OpportunityCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    source_id: str
    allow_low_confidence: bool = False


class OpportunityGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = 5


class OpportunityEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    working_title: Optional[str] = None
    selected_angle: Optional[str] = None
    notes: Optional[str] = None
    priority: Optional[int] = None
    target_format: Optional[str] = None
    target_duration_min: Optional[int] = None
    target_duration_max: Optional[int] = None


class OpportunityStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    rejection_reason: Optional[str] = None
    note: Optional[str] = None


class ProductionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: int


class ProductionEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: Optional[int] = None
    manual_notes: Optional[str] = None
    rights_gate_status: Optional[str] = None
    note: Optional[str] = None


class ProductionStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    blocker_reason: Optional[str] = None
    note: Optional[str] = None


class ProductionTaskEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_notes: Optional[str] = None
    output: Optional[dict[str, Any]] = None


class ProductionTaskStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    note: Optional[str] = None


class ProductionBlueprintBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_minutes: Optional[float] = None
    narration_wpm: Optional[int] = None


class ProductionSectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint_asset_id: Optional[int] = None


class ProductionAssetReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = None


class ProductionRenderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_video_path: str
    source_media_rights: str
    voice_id: Optional[str] = None
    speaker_mapping: Optional[dict[str, str]] = None
    preserve_source_audio: bool = False
    source_subtitle_boxes: Optional[list[dict[str, Any]]] = None


class ProductionPublishingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_job_id: int
    metadata_asset_id: Optional[int] = None
    thumbnail_path: Optional[str] = None
    privacy: str = "private"
    schedule_local: Optional[str] = None
    schedule_timezone: Optional[str] = None
    dry_run: bool = True
    confirm_publish: bool = False
    confirm_public: bool = False


class PerformanceRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_window_hours: Optional[float] = None


class LearningRecommendationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = None


class FacebookPublishingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_job_id: int
    metadata_asset_id: Optional[int] = None
    destination_type: str = "page_video"
    schedule_local: Optional[str] = None
    schedule_timezone: Optional[str] = None
    dry_run: bool = True
    confirm_publish: bool = False


class FacebookPageSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str


class AutomationStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "MANUAL_STEP"
    configuration: dict[str, Any]
    trigger: str = "manual"
    run_type: str = "content_pipeline"
    channel_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    start_now: bool = False


class AutomationApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refs: Optional[dict[str, Any]] = None
    note: Optional[str] = None


class AutonomousConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_key: str
    enabled: bool = False
    mode: str = "FULL_AUTONOMOUS"
    provider_mode: str = "DRY_RUN"
    cadence: str = "daily"
    local_time: str = "09:00"
    timezone_name: str = "UTC"
    missed_run_policy: str = "run_once"
    configuration: dict[str, Any] = {}


class AutonomousCycleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: Optional[str] = None
    start_now: bool = True


def _store_from_request(request: Request) -> Store:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Application storage is unavailable.")
    return store


def _production_render_call(operation):
    try:
        return operation()
    except ProductionRenderNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionRenderError as exc:
        raise HTTPException(409, str(exc)) from exc


def _production_publishing_call(operation):
    try:
        return operation()
    except ProductionPublishingNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionPublishingError as exc:
        raise HTTPException(409, str(exc)) from exc


def _feedback_learning_call(operation):
    try:
        return operation()
    except FeedbackLearningNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except FeedbackLearningError as exc:
        raise HTTPException(409, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


def _facebook_publishing_call(operation):
    try:
        return operation()
    except FacebookPublishingNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except FacebookPublishingError as exc:
        raise HTTPException(409, str(exc)) from exc


def _automation_call(operation):
    try:
        return operation()
    except AutomationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except AutomationError as exc:
        raise HTTPException(409, str(exc)) from exc


def _autonomous_call(operation):
    try:
        return operation()
    except AutonomousOperatorNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except AutonomousOperatorError as exc:
        raise HTTPException(409, str(exc)) from exc


def _service(store: Store) -> YouTubeReadOnlyService:
    return YouTubeReadOnlyService(GoogleOAuthTokenService(store))


def _trend_service(store: Store) -> YouTubeTrendScanner:
    youtube = _service(store)
    return YouTubeTrendScanner(store, YouTubeTrendSearchProvider(youtube))


def _competitor_service(store: Store) -> CompetitorIntelligenceService:
    youtube = _service(store)
    return CompetitorIntelligenceService(store, YouTubeCompetitorProvider(youtube))


def _brain_provider() -> OllamaProvider:
    settings = config.channel_agent_brain_settings()
    return OllamaProvider(
        enabled=bool(settings["enabled"]),
        base_url=str(settings["base_url"]),
        model=str(settings["model"]),
        timeout_seconds=float(settings["timeout_seconds"]),
    )


def _own_channel_context(store: Store, user_id: int) -> dict[str, Any]:
    youtube = _service(store)
    if not youtube.connection_status(user_id).connected:
        return {}
    start, end = default_date_range(28)
    channel = youtube.get_own_channel(user_id).to_dict()
    channel["last_28_days"] = youtube.get_overview(user_id, start, end).to_dict()
    return channel


def _brain_service(store: Store) -> ContentBrainService:
    settings = config.channel_agent_brain_settings()
    return ContentBrainService(
        store,
        _brain_provider(),
        max_evidence_items=int(settings["max_evidence_items"]),
        max_prompt_chars=int(settings["max_prompt_chars"]),
        temperature_analysis=float(settings["temperature_analysis"]),
        temperature_creative=float(settings["temperature_creative"]),
        repair_temperature=float(settings["repair_temperature"]),
        top_p=float(settings["top_p"]),
        num_predict_by_mode={
            str(mode): int(value)
            for mode, value in dict(settings["num_predict_by_mode"]).items()
        },
        own_context_loader=lambda user_id: _own_channel_context(store, user_id),
    )


def _opportunity_service(store: Store) -> ContentOpportunityService:
    return ContentOpportunityService(store)


def _production_service(store: Store) -> ProductionQueueService:
    return ProductionQueueService(store)


def _production_asset_service(store: Store) -> ProductionAssetService:
    settings = config.channel_agent_production_settings()
    brain = config.channel_agent_brain_settings()
    provider = OllamaProvider(
        enabled=bool(brain["enabled"]),
        base_url=str(brain["base_url"]),
        model=str(brain["model"]),
        timeout_seconds=float(settings["timeout_seconds"]),
    )
    return ProductionAssetService(
        store, provider,
        narration_wpm=int(settings["narration_wpm"]),
        minimum_word_ratio=float(settings["minimum_word_ratio"]),
        max_continuations=int(settings["max_continuations"]),
        temperature=float(settings["temperature"]),
        repair_temperature=float(settings["repair_temperature"]),
        top_p=float(settings["top_p"]),
        blueprint_num_predict=int(settings["blueprint_num_predict"]),
        section_num_predict=int(settings["section_num_predict"]),
        asset_num_predict=int(settings["asset_num_predict"]),
    )


def _feedback_learning_service(store: Store) -> FeedbackLearningService:
    return FeedbackLearningService(store, llm=_brain_provider())


def _automation_service(store: Store) -> AutomationOrchestrator:
    def research(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        result = _trend_service(store).scan(user_id)
        return {"trend_scan_id": result.get("scan_id"), "research_result": result}

    def competitor(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        service = _competitor_service(store)
        if config.get("discover_competitors", True):
            service.discover(user_id)
        result = service.refresh(user_id, mode=str(config.get("competitor_mode", "long")))
        return {"competitor_result": result}

    def script(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        if not config.get("auto_generate_assets", False):
            raise AutomationWaiting("Generate the Script Draft explicitly, then approve it.")
        item_id = int(refs.get("production_item_id") or config.get("production_item_id") or 0)
        service = _production_asset_service(store)
        service.generate_blueprint(user_id, item_id)
        service.resume_script(user_id, item_id)
        result = service.assemble_script(user_id, item_id)
        return {"script_asset_id": int(result["asset"]["id"] if "asset" in result else result["id"])}

    def assets(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        if not config.get("auto_generate_assets", False):
            raise AutomationWaiting("Generate and review production assets explicitly.")
        item_id = int(refs.get("production_item_id") or config.get("production_item_id") or 0)
        item = store.get_production_item(user_id, item_id)
        service = _production_asset_service(store)
        generated = {
            "visual_plan": service.generate_visual_plan(user_id, item_id),
            "voice_plan": service.generate_voice_plan(user_id, item_id),
            "metadata_package": service.generate_metadata_package(user_id, item_id),
        }
        if item and item.get("target_format") != "short_form":
            generated["thumbnail_brief"] = service.generate_thumbnail_brief(user_id, item_id)
        return {"generated_asset_ids": {
            name: int(value["asset"]["id"] if "asset" in value else value["id"])
            for name, value in generated.items()
        }}

    def render(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        if not config.get("auto_render_after_approval", False):
            raise AutomationWaiting("Start the approved CP7B render explicitly.")
        request = config.get("render_request")
        if not isinstance(request, dict):
            raise AutomationWaiting("A reviewed render request is required.")
        item_id = int(refs.get("production_item_id") or config.get("production_item_id") or 0)
        service = ProductionRenderService(store)
        job = service.submit(user_id, item_id, **request)
        result = service.run(user_id, item_id, int(job["id"]))
        return {"render_job_id": int(result["id"])}

    def publish(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        item_id = int(refs.get("production_item_id") or config.get("production_item_id") or 0)
        render_id = int(refs.get("render_job_id") or 0)
        existing = store.list_production_publishing_jobs(user_id, item_id)
        results: dict[str, Any] = {}
        for platform in config.get("target_platforms") or []:
            prior = next((row for row in existing if row.get("platform") == platform
                          and row.get("status") != "cancelled"), None)
            if prior:
                results[platform] = {"job_id": int(prior["id"]), "status": prior["status"], "reused": True}
                continue
            try:
                if platform == "youtube":
                    privacy = str(config.get("default_publishing_privacy", "private"))
                    service: Any = ProductionPublishingService(store)
                    job = service.submit(
                        user_id, item_id, render_job_id=render_id, privacy=privacy,
                        schedule_local=config.get("schedule_local"),
                        schedule_timezone=config.get("schedule_timezone"), dry_run=False,
                        confirm_publish=True,
                        confirm_public=bool(config.get("confirm_public", False)),
                    )
                else:
                    service = FacebookPublishingService(store)
                    job = service.submit(
                        user_id, item_id, render_job_id=render_id,
                        destination_type=str(config.get("facebook_destination", "page_video")),
                        schedule_local=config.get("schedule_local"),
                        schedule_timezone=config.get("schedule_timezone"), dry_run=False,
                        confirm_publish=True,
                    )
                result = service.run(user_id, item_id, int(job["id"]))
                results[platform] = {"job_id": int(result["id"]), "status": result["status"]}
            except Exception as exc:
                failed = next((row for row in store.list_production_publishing_jobs(user_id, item_id)
                               if row.get("platform") == platform), None)
                results[platform] = {
                    "job_id": int(failed["id"]) if failed else None,
                    "status": failed["status"] if failed else "failed", "error": str(exc),
                }
        return {"publishing_jobs": results,
                "partial_success": any(row["status"] != "failed" for row in results.values())
                and any(row["status"] == "failed" for row in results.values())}

    def analytics(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        youtube = (refs.get("publishing_jobs") or {}).get("youtube")
        if not youtube or not youtube.get("job_id"):
            raise AutomationWaiting("A linked YouTube publishing job is required for analytics.")
        item_id = int(refs.get("production_item_id") or config.get("production_item_id") or 0)
        result = _feedback_learning_service(store).refresh(
            user_id, item_id, int(youtube["job_id"])
        )
        return {"snapshot_id": int(result["snapshot"]["id"])}

    def learning(user_id: int, config: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        youtube = (refs.get("publishing_jobs") or {}).get("youtube")
        if not youtube or not youtube.get("job_id"):
            raise AutomationWaiting("A linked YouTube publishing job is required for learning.")
        item_id = int(refs.get("production_item_id") or config.get("production_item_id") or 0)
        result = _feedback_learning_service(store).generate_report(
            user_id, item_id, int(youtube["job_id"])
        )
        return {"learning_report_id": int(result["id"])}

    return AutomationOrchestrator(store, handlers={
        "research": research, "competitor": competitor,
        "script": script, "assets": assets, "render": render,
        "publish": publish, "analytics": analytics, "learning": learning,
    })


def _autonomous_service(store: Store) -> AutonomousChannelOperator:
    return AutonomousChannelOperator(store, _automation_service(store))


def _production_asset_call(call: Any) -> Any:
    try:
        return call()
    except ProductionAssetNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionAssetError as exc:
        raise HTTPException(422, str(exc)) from exc


def _validate_trend_query(data: dict[str, Any]) -> dict[str, Any]:
    if "query" in data:
        data["query"] = str(data["query"] or "").strip()
        if not data["query"] or len(data["query"]) > 200:
            raise HTTPException(422, "Research query must contain 1–200 characters.")
    if data.get("published_within_days") is not None and not 1 <= int(data["published_within_days"]) <= 365:
        raise HTTPException(422, "published_within_days must be between 1 and 365.")
    if data.get("duration_filter") is not None and data["duration_filter"] not in {"any", "short", "medium", "long"}:
        raise HTTPException(422, "Unsupported duration_filter.")
    if data.get("search_order") is not None and data["search_order"] not in {"relevance", "date", "viewCount"}:
        raise HTTPException(422, "Unsupported search_order.")
    for field in ("topic_terms", "exclusion_terms"):
        if field in data and data[field] is not None:
            data[field] = str(data[field]).strip() or None
            if data[field] and len(data[field]) > 2000:
                raise HTTPException(422, f"{field} must contain at most 2000 characters.")
    return data


def _require_enabled() -> None:
    if not config.is_ai_channel_agent_enabled():
        raise HTTPException(404, "AI Channel Agent is disabled.")


def _dates(days: int, start_date: Optional[date], end_date: Optional[date]) -> tuple[date, date]:
    if start_date is None and end_date is None:
        return default_date_range(days)
    if start_date is None or end_date is None:
        raise HTTPException(422, "Provide both start_date and end_date, or neither.")
    if start_date > end_date:
        raise HTTPException(422, "start_date must not be after end_date.")
    if (end_date - start_date).days > 365:
        raise HTTPException(422, "Date range must not exceed 366 days.")
    return start_date, end_date


def _provider_call(call: Any) -> Any:
    try:
        return call()
    except YouTubeReadOnlyError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/status", response_model=ChannelAgentStatusResponse)
def channel_agent_status(
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> ChannelAgentStatusResponse:
    """Return cheap local credential state; no Google network probe."""

    enabled = config.is_ai_channel_agent_enabled()
    youtube = _service(store).connection_status(user_id) if enabled else None
    ollama = _brain_provider().status() if enabled else None
    status = ChannelAgentService(
        enabled=enabled,
        youtube_connected=bool(youtube and youtube.connected),
        youtube_credential_present=bool(youtube and youtube.credential_present),
        youtube_connection_verified=(youtube.connection_verified if youtube else None),
        ollama_available=(ollama.reachable and ollama.model_available) if ollama else None,
    ).status()
    return ChannelAgentStatusResponse(
        **status.to_dict(), ollama=ollama.to_dict() if ollama else None
    )


@router.get("/brain/status")
def content_brain_status(
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    del user_id
    _require_enabled()
    return _brain_provider().status().to_dict()


@router.post("/brain/analyze")
def analyze_content_brain(
    body: ContentBrainAnalyzeBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if body.request_type not in REQUEST_TYPES:
        raise HTTPException(422, "Unsupported Content Brain request type.")
    if body.selector_type not in SELECTOR_TYPES:
        raise HTTPException(422, "Unsupported Content Brain selector.")
    if body.selector_id is not None and len(body.selector_id) > 240:
        raise HTTPException(422, "Content Brain selector is too long.")
    try:
        return _brain_service(store).analyze(
            user_id,
            request_type=body.request_type,
            selector_type=body.selector_type,
            selector_id=body.selector_id,
            allow_low_confidence=body.allow_low_confidence,
        )
    except ContentBrainAlreadyRunning as exc:
        raise HTTPException(409, str(exc)) from exc
    except EvidenceSelectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OllamaTimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except (OllamaProviderError, ContentBrainInvalidResponse) as exc:
        raise HTTPException(503, str(exc)) from exc
    except ContentBrainError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/brain/runs")
def content_brain_runs(
    limit: int = Query(30, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return store.list_content_brain_runs(user_id, limit)


@router.get("/brain/runs/{run_id}")
def content_brain_run(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    run = store.get_content_brain_run(user_id, run_id)
    if not run:
        raise HTTPException(404, "Content Brain run not found.")
    return run


@router.delete("/brain/runs/{run_id}", status_code=204)
def delete_content_brain_run(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> None:
    _require_enabled()
    if not store.delete_content_brain_run(user_id, run_id):
        raise HTTPException(404, "Content Brain run not found.")


@router.get("/opportunities")
def content_opportunities(
    status: Optional[str] = None,
    confidence: Optional[str] = None,
    competition: Optional[str] = None,
    source_type: Optional[str] = None,
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    limit: int = Query(20, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    statuses = (sorted(STATUSES) if status == "all" else
                ([part.strip() for part in status.split(",") if part.strip()]
                 if status else ["draft", "watch", "approved"]))
    if any(value not in STATUSES for value in statuses):
        raise HTTPException(422, "Unsupported opportunity status filter.")
    if confidence and confidence not in CONFIDENCE_LEVELS:
        raise HTTPException(422, "Unsupported evidence confidence filter.")
    if competition and competition not in COMPETITION_LEVELS:
        raise HTTPException(422, "Unsupported competition filter.")
    if source_type and source_type not in SOURCE_TYPES:
        raise HTTPException(422, "Unsupported opportunity source filter.")
    return _opportunity_service(store).list(
        user_id, statuses=statuses, confidence=confidence, competition=competition,
        source_type=source_type, min_score=min_score, limit=limit,
    )


@router.post("/opportunities")
def create_content_opportunity(
    body: OpportunityCreateBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        opportunity, created = _opportunity_service(store).create(
            user_id, source_type=body.source_type, source_id=body.source_id,
            allow_low_confidence=body.allow_low_confidence,
        )
    except OpportunityError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"created": created, "opportunity": opportunity}


@router.post("/opportunities/generate")
def generate_content_opportunities(
    body: OpportunityGenerateBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if not 1 <= body.limit <= 20:
        raise HTTPException(422, "Opportunity generation limit must be between 1 and 20.")
    return _opportunity_service(store).generate(user_id, limit=body.limit)


@router.get("/opportunities/{opportunity_id}")
def content_opportunity_detail(
    opportunity_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _opportunity_service(store).get(user_id, opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/opportunities/{opportunity_id}")
def edit_content_opportunity(
    opportunity_id: int,
    body: OpportunityEditBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _opportunity_service(store).edit(
            user_id, opportunity_id, **body.model_dump(exclude_unset=True)
        )
    except OpportunityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OpportunityError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/opportunities/{opportunity_id}/status")
def change_content_opportunity_status(
    opportunity_id: int,
    body: OpportunityStatusBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _opportunity_service(store).change_status(
            user_id, opportunity_id, status=body.status,
            rejection_reason=body.rejection_reason, note=body.note,
        )
    except OpportunityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OpportunityError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/opportunities/{opportunity_id}/refresh")
def refresh_content_opportunity(
    opportunity_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _opportunity_service(store).refresh(user_id, opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except OpportunityError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/opportunities/{opportunity_id}", status_code=204)
def delete_content_opportunity(
    opportunity_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> None:
    _require_enabled()
    try:
        _opportunity_service(store).delete(user_id, opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/production")
def production_queue(
    status: Optional[str] = None,
    min_priority: int = Query(0, ge=0, le=100),
    rights: Optional[str] = None,
    target_format: Optional[str] = None,
    opportunity_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=50),
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    statuses = (
        sorted(ITEM_STATUSES) if status == "all"
        else [part.strip() for part in status.split(",") if part.strip()]
        if status else sorted(ACTIVE_ITEM_STATUSES)
    )
    if any(value not in ITEM_STATUSES for value in statuses):
        raise HTTPException(422, "Unsupported production status filter.")
    if rights and rights not in RIGHTS_GATES:
        raise HTTPException(422, "Unsupported rights gate filter.")
    if target_format and target_format not in {"long_form", "short_form", "all", "unspecified"}:
        raise HTTPException(422, "Unsupported production format filter.")
    return _production_service(store).list(
        user_id, statuses=statuses, min_priority=min_priority, rights=rights,
        target_format=target_format, opportunity_id=opportunity_id, limit=limit,
    )


@router.post("/production")
def create_production_item(
    body: ProductionCreateBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        item, created = _production_service(store).create(user_id, body.opportunity_id)
    except ProductionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"created": created, "production_item": item}


@router.get("/production/{item_id}")
def production_item_detail(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _production_service(store).get(user_id, item_id)
    except ProductionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/production/{item_id}")
def edit_production_item(
    item_id: int,
    body: ProductionEditBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _production_service(store).edit(
            user_id, item_id, **body.model_dump(exclude_unset=True)
        )
    except ProductionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/production/{item_id}/sync")
def sync_production_item(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _production_service(store).sync(user_id, item_id)
    except ProductionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/production/{item_id}/status")
def change_production_item_status(
    item_id: int,
    body: ProductionStatusBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if body.status not in ITEM_STATUSES:
        raise HTTPException(422, "Unsupported production status.")
    if body.blocker_reason and body.blocker_reason not in BLOCKER_REASONS:
        raise HTTPException(422, "Unsupported blocker reason.")
    try:
        return _production_service(store).change_status(
            user_id, item_id, status=body.status,
            blocker_reason=body.blocker_reason, note=body.note,
        )
    except ProductionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/production/{item_id}/tasks")
def production_tasks(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return production_item_detail(item_id, user_id, store)["tasks"]


@router.patch("/production/{item_id}/tasks/{task_id}")
def edit_production_task(
    item_id: int,
    task_id: int,
    body: ProductionTaskEditBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _production_service(store).edit_task(
            user_id, item_id, task_id, **body.model_dump(exclude_unset=True)
        )
    except ProductionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/production/{item_id}/tasks/{task_id}/status")
def change_production_task_status(
    item_id: int,
    task_id: int,
    body: ProductionTaskStatusBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if body.status not in TASK_STATUSES:
        raise HTTPException(422, "Unsupported production task status.")
    try:
        return _production_service(store).change_task_status(
            user_id, item_id, task_id, status=body.status, note=body.note,
        )
    except ProductionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/production/{item_id}/events")
def production_events(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return production_item_detail(item_id, user_id, store)["events"]


@router.get("/production/{item_id}/assets")
def production_assets(
    item_id: int, asset_type: Optional[str] = None, asset_key: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    if asset_type and asset_type not in ASSET_TYPES:
        raise HTTPException(422, "Unsupported production asset type.")
    return _production_asset_call(lambda: _production_asset_service(store).list_assets(
        user_id, item_id, asset_type=asset_type, asset_key=asset_key))


@router.get("/production/{item_id}/assets/{asset_id}")
def production_asset_detail(
    item_id: int, asset_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).get_asset(user_id, item_id, asset_id))


@router.post("/production/{item_id}/assets/script/blueprints")
def generate_script_blueprint(
    item_id: int, body: ProductionBlueprintBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).generate_blueprint(
        user_id, item_id, **body.model_dump(exclude_unset=True)))


@router.get("/production/{item_id}/assets/script/blueprints")
def script_blueprint_versions(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).list_assets(
        user_id, item_id, asset_type="script_blueprint"))


@router.post("/production/{item_id}/assets/script/sections/{section_index}")
def generate_script_section(
    item_id: int, section_index: int, body: ProductionSectionBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).generate_section(
        user_id, item_id, section_index, **body.model_dump(exclude_unset=True)))


@router.post("/production/{item_id}/assets/script/sections/{section_index}/regenerate")
def regenerate_script_section(
    item_id: int, section_index: int, body: ProductionSectionBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return generate_script_section(item_id, section_index, body, user_id, store)


@router.get("/production/{item_id}/assets/script/sections/{section_index}")
def script_section_versions(
    item_id: int, section_index: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).list_assets(
        user_id, item_id, asset_type="script_section", asset_key=f"{section_index:02d}"))


@router.post("/production/{item_id}/assets/script/resume")
def resume_script_generation(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).queue_resume_script(user_id, item_id))


@router.post("/production/{item_id}/assets/script/drafts")
def assemble_script_draft(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).assemble_script(user_id, item_id))


@router.get("/production/{item_id}/assets/script/drafts")
def script_draft_versions(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).list_assets(
        user_id, item_id, asset_type="script_draft"))


@router.post("/production/{item_id}/assets/{asset_id}/approve")
def approve_production_asset(
    item_id: int, asset_id: int, body: ProductionAssetReviewBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).review_asset(
        user_id, item_id, asset_id, decision="approved", note=body.note))


@router.post("/production/{item_id}/assets/{asset_id}/review")
def submit_production_asset_for_review(
    item_id: int, asset_id: int, body: ProductionAssetReviewBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).submit_for_review(
        user_id, item_id, asset_id, note=body.note))


@router.post("/production/{item_id}/assets/{asset_id}/reject")
def reject_production_asset(
    item_id: int, asset_id: int, body: ProductionAssetReviewBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).review_asset(
        user_id, item_id, asset_id, decision="rejected", note=body.note))


def _generate_asset_route(
    item_id: int, asset_type: str, user_id: int, store: Store,
) -> dict[str, Any]:
    service = _production_asset_service(store)
    method = {
        "visual-plan": service.generate_visual_plan,
        "voice-plan": service.generate_voice_plan,
        "thumbnail-brief": service.generate_thumbnail_brief,
        "metadata-package": service.generate_metadata_package,
    }[asset_type]
    return _production_asset_call(lambda: method(user_id, item_id))


@router.post("/production/{item_id}/assets/generate/{asset_type}")
def generate_production_asset(
    item_id: int, asset_type: str,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if asset_type not in {
        "visual-plan", "voice-plan", "thumbnail-brief", "metadata-package"
    }:
        raise HTTPException(422, "Unsupported generated production asset.")
    return _generate_asset_route(item_id, asset_type, user_id, store)


@router.get("/production/{item_id}/generation-jobs")
def production_generation_jobs(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).list_jobs(user_id, item_id))


@router.get("/production/{item_id}/generation-jobs/{job_id}")
def production_generation_job(
    item_id: int, job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).get_job(user_id, item_id, job_id))


@router.get("/production/{item_id}/asset-package")
def production_asset_package(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).package(user_id, item_id))


@router.post("/production/{item_id}/render-jobs")
def submit_production_render(
    item_id: int, body: ProductionRenderBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_render_call(lambda: ProductionRenderService(store).submit(
        user_id, item_id,
        source_video_path=body.source_video_path,
        source_media_rights=body.source_media_rights,
        voice_id=body.voice_id,
        speaker_mapping=body.speaker_mapping,
        preserve_source_audio=body.preserve_source_audio,
        source_subtitle_boxes=body.source_subtitle_boxes,
    ))


@router.get("/production/{item_id}/render-jobs")
def production_render_jobs(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _production_render_call(
        lambda: ProductionRenderService(store).list(user_id, item_id)
    )


@router.get("/production/{item_id}/render-jobs/{job_id}")
def production_render_job(
    item_id: int, job_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_render_call(
        lambda: ProductionRenderService(store).get(user_id, item_id, job_id)
    )


@router.post("/production/{item_id}/render-jobs/{job_id}/run")
def run_production_render(
    item_id: int, job_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_render_call(
        lambda: ProductionRenderService(store).run(user_id, item_id, job_id)
    )


@router.post("/production/{item_id}/render-jobs/{job_id}/resume")
def resume_production_render(
    item_id: int, job_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_render_call(
        lambda: ProductionRenderService(store).resume(user_id, item_id, job_id)
    )


@router.get("/publishing/youtube/connection")
def production_publishing_connection(
    verify: bool = Query(False), user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_publishing_call(
        lambda: ProductionPublishingService(store).connection(user_id, verify=verify)
    )


@router.get("/publishing/capabilities")
def social_publishing_capabilities(
    verify: bool = Query(False), user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    youtube = _production_publishing_call(
        lambda: ProductionPublishingService(store).connection(user_id, verify=verify)
    )
    facebook = _facebook_publishing_call(
        lambda: FacebookPublishingService(store).connection(user_id, verify=verify)
    )
    return {"youtube": {
        **youtube, "connected": bool(youtube.get("connected")),
        "upload_supported": bool(youtube.get("upload_scope_granted")),
        "schedule_supported": bool(youtube.get("upload_scope_granted")),
        "analytics_supported": bool(youtube.get("connected")),
    }, "facebook": facebook}


@router.get("/publishing/facebook/pages")
def facebook_managed_pages(
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _facebook_publishing_call(lambda: FacebookPublishingService(store).pages(user_id))


@router.post("/publishing/facebook/pages/select")
def select_facebook_page(
    body: FacebookPageSelectionBody, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _facebook_publishing_call(
        lambda: FacebookPublishingService(store).select_page(user_id, body.page_id)
    )


@router.post("/production/{item_id}/publishing-jobs")
def submit_production_publishing(
    item_id: int, body: ProductionPublishingBody,
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_publishing_call(lambda: ProductionPublishingService(store).submit(
        user_id, item_id, render_job_id=body.render_job_id,
        metadata_asset_id=body.metadata_asset_id, thumbnail_path=body.thumbnail_path,
        privacy=body.privacy, schedule_local=body.schedule_local,
        schedule_timezone=body.schedule_timezone, dry_run=body.dry_run,
        confirm_publish=body.confirm_publish, confirm_public=body.confirm_public,
    ))


@router.post("/production/{item_id}/publishing-jobs/facebook")
def submit_facebook_publishing_job(
    item_id: int, body: FacebookPublishingBody,
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _facebook_publishing_call(lambda: FacebookPublishingService(store).submit(
        user_id, item_id, render_job_id=body.render_job_id,
        metadata_asset_id=body.metadata_asset_id,
        destination_type=body.destination_type,
        schedule_local=body.schedule_local, schedule_timezone=body.schedule_timezone,
        dry_run=body.dry_run, confirm_publish=body.confirm_publish,
    ))


@router.get("/production/{item_id}/publishing-jobs")
def production_publishing_jobs(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    if not store.get_production_item(user_id, item_id):
        raise HTTPException(404, "Production item not found.")
    return store.list_production_publishing_jobs(user_id, item_id)


@router.get("/production/{item_id}/publishing-jobs/{job_id}")
def production_publishing_job(
    item_id: int, job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    job = store.get_production_publishing_job(user_id, item_id, job_id)
    if not job:
        raise HTTPException(404, "Publishing job not found.")
    return {**job, "destination": store.get_publishing_destination(user_id, job_id),
            "result": store.get_publishing_result(user_id, job_id)}


@router.post("/production/{item_id}/publishing-jobs/{job_id}/{action}")
def mutate_production_publishing_job(
    item_id: int, job_id: int, action: str,
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    job = store.get_production_publishing_job(user_id, item_id, job_id)
    if not job:
        raise HTTPException(404, "Publishing job not found.")
    service: Any = (FacebookPublishingService(store) if job.get("platform") == "facebook"
                    else ProductionPublishingService(store))
    operations = {"run": service.run, "retry": service.run, "refresh": service.refresh, "cancel": service.cancel}
    if action not in operations:
        raise HTTPException(422, "Publishing action must be run, retry, refresh, or cancel.")
    call = _facebook_publishing_call if job.get("platform") == "facebook" else _production_publishing_call
    return call(lambda: operations[action](user_id, item_id, job_id))


@router.post("/production/{item_id}/publishing-jobs/{job_id}/performance/refresh")
def refresh_video_performance(
    item_id: int, job_id: int, body: PerformanceRefreshBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _feedback_learning_call(lambda: _feedback_learning_service(store).refresh(
        user_id, item_id, job_id,
        evaluation_window_hours=body.evaluation_window_hours))


@router.get("/production/{item_id}/publishing-jobs/{job_id}/performance/snapshots")
def video_performance_snapshots(
    item_id: int, job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).snapshots(user_id, item_id, job_id))


@router.get("/production/{item_id}/publishing-jobs/{job_id}/performance/snapshots/latest")
def latest_video_performance(
    item_id: int, job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).latest(user_id, item_id, job_id))


@router.get("/production/{item_id}/learning")
def production_learning_summary(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).item_summary(user_id, item_id))


@router.get("/learning/profile")
def channel_learning_profile(
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).profile(user_id))


@router.post("/production/{item_id}/publishing-jobs/{job_id}/learning/reports")
def generate_video_learning_report(
    item_id: int, job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).generate_report(
            user_id, item_id, job_id))


@router.get("/production/{item_id}/publishing-jobs/{job_id}/learning/reports")
def video_learning_reports(
    item_id: int, job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).reports(
            user_id, item_id, job_id))


@router.post(
    "/production/{item_id}/publishing-jobs/{job_id}/learning/reports/{report_id}"
    "/recommendations/{recommendation_id}/{action}"
)
def review_learning_recommendation(
    item_id: int, job_id: int, report_id: int, recommendation_id: str,
    action: str, body: LearningRecommendationBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if action not in {"apply", "ignore"}:
        raise HTTPException(422, "Learning action must be apply or ignore.")
    return _feedback_learning_call(
        lambda: _feedback_learning_service(store).review_recommendation(
            user_id, item_id, job_id, report_id, recommendation_id,
            "applied" if action == "apply" else "ignored", body.note))


@router.post("/automation/runs")
def start_automation_run(
    body: AutomationStartBody, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    def operation() -> dict[str, Any]:
        service = _automation_service(store)
        run, created = service.start(
            user_id, mode=body.mode, configuration=body.configuration,
            trigger=body.trigger, run_type=body.run_type, channel_id=body.channel_id,
            idempotency_key=body.idempotency_key,
        )
        if body.start_now and created:
            run = service.advance(user_id, int(run["id"]))
        return {"run": run, "created": created}
    return _automation_call(operation)


@router.get("/automation/runs")
def automation_runs(
    limit: int = Query(50, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _automation_call(lambda: _automation_service(store).list(user_id, limit=limit))


@router.get("/automation/runs/{run_id}")
def automation_run(
    run_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _automation_call(lambda: _automation_service(store).get(user_id, run_id))


@router.post("/automation/runs/{run_id}/{action}")
def mutate_automation_run(
    run_id: int, action: str, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    service = _automation_service(store)
    operations = {
        "advance": service.advance, "resume": service.resume, "pause": service.pause,
        "retry": service.retry, "cancel": service.cancel,
    }
    if action not in operations:
        raise HTTPException(422, "Automation action must be advance, resume, pause, retry, or cancel.")
    return _automation_call(lambda: operations[action](user_id, run_id))


@router.post("/automation/runs/{run_id}/approvals/{stage}")
def approve_automation_gate(
    run_id: int, stage: str, body: AutomationApprovalBody,
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _automation_call(lambda: _automation_service(store).approve(
        user_id, run_id, stage, refs=body.refs, note=body.note
    ))


@router.post("/autonomous/configs")
def save_autonomous_config(
    body: AutonomousConfigBody, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _autonomous_call(lambda: _autonomous_service(store).save_config(
        user_id, body.channel_key, enabled=body.enabled, mode=body.mode,
        provider_mode=body.provider_mode, cadence=body.cadence,
        local_time=body.local_time, timezone_name=body.timezone_name,
        missed_run_policy=body.missed_run_policy, configuration=body.configuration,
    ))


@router.get("/autonomous/configs")
def autonomous_configs(
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _autonomous_call(lambda: _autonomous_service(store).list_configs(user_id))


@router.post("/autonomous/configs/{config_id}/cycles")
def start_autonomous_cycle(
    config_id: int, body: AutonomousCycleBody,
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    def operation() -> dict[str, Any]:
        cycle, created = _autonomous_service(store).start_cycle(
            user_id, config_id, trigger="manual", idempotency_key=body.idempotency_key,
            advance=body.start_now,
        )
        return {"cycle": cycle, "created": created}
    return _autonomous_call(operation)


@router.post("/autonomous/configs/{config_id}/{action}")
def control_autonomous_config(
    config_id: int, action: str,
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if action not in {"pause", "resume", "stop-after-current"}:
        raise HTTPException(422, "Action must be pause, resume, or stop-after-current.")
    enabled = action == "resume"
    stop_after = action == "stop-after-current"
    return _autonomous_call(
        lambda: _autonomous_service(store).set_enabled(
            user_id, config_id, enabled, stop_after_current=stop_after,
        )
    )


@router.get("/autonomous/cycles")
def autonomous_cycles(
    limit: int = Query(50, ge=1, le=100), user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _autonomous_call(lambda: _autonomous_service(store).list_cycles(user_id, limit))


@router.get("/autonomous/cycles/{cycle_id}")
def autonomous_cycle(
    cycle_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _autonomous_call(lambda: _autonomous_service(store).get_cycle(user_id, cycle_id))


@router.post("/autonomous/cycles/{cycle_id}/advance")
def advance_autonomous_cycle(
    cycle_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _autonomous_call(lambda: _autonomous_service(store).advance_cycle(user_id, cycle_id))


@router.post("/autonomous/scheduler/tick")
def tick_autonomous_scheduler(
    user_id: int = Depends(get_current_user_id), store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    service = _autonomous_service(store)
    return service.run_due(user_id=user_id)


@router.get("/autonomous/provider-cost")
def autonomous_provider_cost(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    _require_enabled()
    return get_cost_report().to_dict()


@router.get("/production/{item_id}/qa")
def inspect_production_asset_qa(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(
        lambda: _production_asset_service(store).inspect_qa(user_id, item_id))


@router.post("/production/{item_id}/qa")
def run_production_asset_qa(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _production_asset_call(lambda: _production_asset_service(store).inspect_qa(
        user_id, item_id, complete_task=True))


@router.get("/youtube/status")
def youtube_status(
    verify: bool = False,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _service(store).connection_status(user_id, verify=verify).to_dict()


@router.get("/youtube/channel")
def youtube_channel(
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _provider_call(lambda: _service(store).get_own_channel(user_id)).to_dict()


@router.get("/youtube/overview")
def youtube_overview(
    days: int = Query(28, ge=1, le=366),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    return _provider_call(lambda: _service(store).get_overview(user_id, start, end)).to_dict()


@router.get("/youtube/top-videos")
def youtube_top_videos(
    days: int = Query(28, ge=1, le=366),
    limit: int = Query(10, ge=1, le=25),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    result = _provider_call(
        lambda: _service(store).get_top_videos(user_id, start, end, limit=limit)
    )
    return [item.to_dict() for item in result]


@router.get("/youtube/traffic-sources")
def youtube_traffic_sources(
    days: int = Query(28, ge=1, le=366),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    result = _provider_call(lambda: _service(store).get_traffic_sources(user_id, start, end))
    return [item.to_dict() for item in result]


@router.get("/youtube/content-type")
def youtube_content_type(
    days: int = Query(28, ge=1, le=366),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    return _provider_call(lambda: _service(store).get_content_types(user_id, start, end)).to_dict()


@router.get("/trends/queries")
def trend_queries(user_id: int = Depends(get_current_user_id),
                  store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    return store.list_trend_queries(user_id)


@router.post("/trends/queries", status_code=201)
def create_trend_query(body: TrendQueryBody, user_id: int = Depends(get_current_user_id),
                       store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    data = _validate_trend_query(body.model_dump())
    try:
        query_id = store.create_trend_query(user_id, **data)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This research query already exists.") from exc
    return next(item for item in store.list_trend_queries(user_id) if item["id"] == query_id)


@router.put("/trends/queries/{query_id}")
def update_trend_query(query_id: int, body: TrendQueryUpdateBody,
                       user_id: int = Depends(get_current_user_id),
                       store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    data = _validate_trend_query(body.model_dump(exclude_unset=True))
    try:
        updated = store.update_trend_query(user_id, query_id, **data)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This research query already exists.") from exc
    if not updated:
        raise HTTPException(404, "Research query not found.")
    return next(item for item in store.list_trend_queries(user_id) if item["id"] == query_id)


@router.delete("/trends/queries/{query_id}", status_code=204)
def delete_trend_query(query_id: int, user_id: int = Depends(get_current_user_id),
                       store: Store = Depends(_store_from_request)) -> None:
    _require_enabled()
    if not store.delete_trend_query(user_id, query_id):
        raise HTTPException(404, "Research query not found.")


@router.post("/trends/scan")
def scan_trends(user_id: int = Depends(get_current_user_id),
                store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    try:
        return _trend_service(store).scan(user_id)
    except TrendScanAlreadyRunning as exc:
        raise HTTPException(409, str(exc)) from exc
    except TrendScanError as exc:
        raise HTTPException(422, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        message = str(exc)
        if exc.code == "youtube_quota_exceeded":
            message = "YouTube API quota is unavailable for this scan. Try again later or reduce the number of research queries."
        raise HTTPException(exc.status_code, {"code": exc.code, "message": message}) from exc


@router.get("/trends/status")
def trend_scan_status(user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    return {
        "last_scan": store.latest_trend_scan(user_id),
        "limits": {"max_queries": MAX_QUERIES_PER_SCAN,
                   "results_per_query": MAX_RESULTS_PER_QUERY,
                   "max_enrichment_channels": MAX_ENRICHMENT_CHANNELS,
                   "min_relevance": trend_min_relevance()},
    }


@router.get("/trends/candidates")
def trend_candidates(limit: int = Query(50, ge=1, le=200),
                     min_score: float = Query(0.0, ge=0.0, le=1.0),
                     min_relevance: Optional[float] = Query(None, ge=0.0, le=1.0),
                     include_filtered: bool = Query(False),
                     user_id: int = Depends(get_current_user_id),
                     store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    threshold = trend_min_relevance() if min_relevance is None else min_relevance
    return store.list_trend_candidates(
        user_id,
        limit=limit,
        min_score=min_score,
        min_relevance=0.0 if include_filtered and min_relevance is None else threshold,
        include_filtered=include_filtered,
    )


@router.get("/trends/candidates/{candidate_id}")
def trend_candidate_detail(candidate_id: int, user_id: int = Depends(get_current_user_id),
                           store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    candidate = store.get_trend_candidate(user_id, candidate_id)
    if not candidate:
        raise HTTPException(404, "Trend candidate not found.")
    candidate["snapshots"] = store.list_trend_snapshots(user_id, candidate_id)
    return candidate


@router.get("/competitors")
def competitors(include_filtered: bool = Query(False),
                user_id: int = Depends(get_current_user_id),
                store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    return store.list_competitors(user_id, limit=MAX_COMPETITORS, include_filtered=include_filtered)


@router.post("/competitors/discover")
def discover_competitors(user_id: int = Depends(get_current_user_id),
                         store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    return _provider_call(lambda: _competitor_service(store).discover(user_id))


@router.post("/competitors", status_code=201)
def add_competitor(body: CompetitorAddBody, user_id: int = Depends(get_current_user_id),
                   store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    if not body.reference.strip() or len(body.reference) > 500:
        raise HTTPException(422, "Enter a valid YouTube channel URL, handle, or channel ID.")
    try:
        return _competitor_service(store).add(user_id, body.reference, body.notes)
    except CompetitorError as exc:
        raise HTTPException(422, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/competitors/refresh")
def refresh_competitors(body: CompetitorRefreshBody,
                        user_id: int = Depends(get_current_user_id),
                        store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    try:
        return _competitor_service(store).refresh(user_id, body.competitor_id, body.mode)
    except CompetitorRefreshRunning as exc:
        raise HTTPException(409, str(exc)) from exc
    except CompetitorError as exc:
        raise HTTPException(422, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        message = str(exc)
        if exc.code == "youtube_quota_exceeded":
            message = "YouTube API quota is unavailable for competitor refresh. Try fewer competitors or refresh later."
        raise HTTPException(exc.status_code, {"code": exc.code, "message": message}) from exc


@router.get("/competitors/gaps")
def competitor_gaps(include_filtered: bool = Query(False),
                    user_id: int = Depends(get_current_user_id),
                    store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    channels = store.list_competitors(user_id, limit=MAX_COMPETITORS, include_filtered=True)
    candidates = store.list_trend_candidates(
        user_id, limit=200, min_relevance=trend_min_relevance(), include_filtered=False,
    )
    return opportunity_gaps(channels, candidates, include_filtered=include_filtered)


@router.get("/competitors/{competitor_id}")
def competitor_detail(competitor_id: int, user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    competitor = store.get_competitor(user_id, competitor_id)
    if not competitor:
        raise HTTPException(404, "Competitor not found.")
    competitor["videos"] = store.list_competitor_videos(user_id, competitor_id, limit=RECENT_VIDEOS)
    competitor["snapshots"] = store.list_competitor_snapshots(user_id, competitor_id)
    return competitor


@router.get("/competitors/{competitor_id}/videos")
def competitor_videos(competitor_id: int, limit: int = Query(20, ge=1, le=100),
                      user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    if not store.get_competitor(user_id, competitor_id):
        raise HTTPException(404, "Competitor not found.")
    return store.list_competitor_videos(user_id, competitor_id, limit)


@router.get("/competitors/{competitor_id}/snapshots")
def competitor_snapshots(competitor_id: int, user_id: int = Depends(get_current_user_id),
                         store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    if not store.get_competitor(user_id, competitor_id):
        raise HTTPException(404, "Competitor not found.")
    return store.list_competitor_snapshots(user_id, competitor_id)


@router.delete("/competitors/{competitor_id}", status_code=204)
def delete_competitor(competitor_id: int, user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> None:
    _require_enabled()
    if not store.delete_competitor(user_id, competitor_id):
        raise HTTPException(404, "Competitor not found.")
