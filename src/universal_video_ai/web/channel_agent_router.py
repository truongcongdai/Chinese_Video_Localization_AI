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
from universal_video_ai.channel_agent.execution import (
    ASSET_STATUSES,
    ProductionExecutionError,
    ProductionExecutionNotFound,
    ProductionExecutionService,
    ProductionGenerationBusy,
)
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


class ProductionAssetEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Optional[dict[str, Any]] = None
    content_text: Optional[str] = None
    manual_notes: Optional[str] = None


class ProductionAssetStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    note: Optional[str] = None
    rejection_reason: Optional[str] = None


def _store_from_request(request: Request) -> Store:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Application storage is unavailable.")
    return store


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


def _execution_service(store: Store) -> ProductionExecutionService:
    settings = config.channel_agent_production_generation_settings()
    return ProductionExecutionService(
        store, _brain_provider(),
        words_per_minute=int(settings["words_per_minute"]),
        default_section_count=int(settings["default_section_count"]),
        max_prompt_chars=int(settings["max_prompt_chars"]),
        temperature=float(settings["temperature"]),
        repair_temperature=float(settings["repair_temperature"]),
        top_p=float(settings["top_p"]),
        num_predict_by_asset={
            str(key): int(value)
            for key, value in dict(settings["num_predict_by_asset"]).items()
        },
    )


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


def _execution_result(call: Any) -> Any:
    try:
        return call()
    except ProductionExecutionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionGenerationBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProductionExecutionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/production/{item_id}/assets")
def production_assets(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).assets(user_id, item_id))


@router.post("/production/{item_id}/script/blueprint")
def generate_script_blueprint(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).start_job(
        user_id, item_id, job_type="script_blueprint", asset_type="script_blueprint"
    ))


@router.post("/production/{item_id}/script/sections/{section_id}/generate")
def generate_script_section(
    item_id: int,
    section_id: str,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if not 2 <= len(section_id) <= 40:
        raise HTTPException(422, "Invalid script section ID.")
    return _execution_result(lambda: _execution_service(store).start_job(
        user_id, item_id, job_type="script_section", asset_type="script_section",
        section_id=section_id,
    ))


@router.post("/production/{item_id}/script/resume")
def resume_script_generation(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).start_job(
        user_id, item_id, job_type="script_resume", asset_type="script_section"
    ))


@router.post("/production/{item_id}/script/assemble")
def assemble_script_draft(
    item_id: int,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).assemble_script(user_id, item_id))


def _start_package_generation(
    item_id: int, asset_type: str, user_id: int, store: Store,
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).start_job(
        user_id, item_id, job_type=asset_type, asset_type=asset_type
    ))


@router.post("/production/{item_id}/visual-plan/generate")
def generate_visual_plan(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    return _start_package_generation(item_id, "visual_plan", user_id, store)


@router.post("/production/{item_id}/voice-plan/generate")
def generate_voice_plan(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    return _start_package_generation(item_id, "voice_plan", user_id, store)


@router.post("/production/{item_id}/thumbnail/generate")
def generate_thumbnail_brief(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    return _start_package_generation(item_id, "thumbnail_brief", user_id, store)


@router.post("/production/{item_id}/metadata/generate")
def generate_metadata_package(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    return _start_package_generation(item_id, "metadata_package", user_id, store)


@router.post("/production/{item_id}/qa")
def create_production_qa(
    item_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).run_qa(user_id, item_id))


@router.get("/assets/{asset_id}")
def production_asset_detail(
    asset_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).get_asset(user_id, asset_id))


@router.patch("/assets/{asset_id}")
def edit_production_asset(
    asset_id: int, body: ProductionAssetEditBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if not body.model_fields_set:
        raise HTTPException(422, "No production asset changes supplied.")
    return _execution_result(lambda: _execution_service(store).manual_version(
        user_id, asset_id, **body.model_dump(exclude_unset=True)
    ))


@router.post("/assets/{asset_id}/status")
def change_production_asset_status(
    asset_id: int, body: ProductionAssetStatusBody,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    if body.status not in ASSET_STATUSES:
        raise HTTPException(422, "Unsupported production asset status.")
    return _execution_result(lambda: _execution_service(store).change_asset_status(
        user_id, asset_id, status=body.status, note=body.note,
        rejection_reason=body.rejection_reason,
    ))


@router.get("/assets/{asset_id}/versions")
def production_asset_versions(
    asset_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).versions(user_id, asset_id))


@router.get("/production-generation/jobs/{job_id}")
def production_generation_job(
    job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).get_job(user_id, job_id))


@router.post("/production-generation/jobs/{job_id}/cancel")
def cancel_production_generation_job(
    job_id: int, user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _execution_result(lambda: _execution_service(store).cancel_job(user_id, job_id))


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
