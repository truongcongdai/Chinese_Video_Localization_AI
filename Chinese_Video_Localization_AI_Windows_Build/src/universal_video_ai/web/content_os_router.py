"""
Content OS API router.

Provides REST API endpoints for Content OS workflow management.
Integrates with the existing web app authentication and user isolation.
"""
import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from universal_video_ai.config import CONTENT_OS_ARTIFACT_DIR, CONTENT_OS_ENABLED, TEMP_DIR
from universal_video_ai.content_os.config import (
    CONTENT_OS_LLM_PROVIDER,
    CONTENT_OS_LLM_MODEL,
    CONTENT_OS_LLM_BASE_URL,
    CONTENT_OS_LLM_API_KEY,
)
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.content_os.artifact_store import ArtifactStore
from universal_video_ai.content_os.workflow import ContentOSWorkflow, WorkflowConfig
from universal_video_ai.content_os.pipeline_adapter import PipelineAdapter
from universal_video_ai.content_os.enums import ApprovalType, ArtifactType, WorkflowStage
from universal_video_ai.content_os.exceptions import FeatureDisabledError
from universal_video_ai.content_os.storyboard import StoryboardManager
from universal_video_ai.content_os.asset_resolver import AssetResolver
from universal_video_ai.content_os.renderer import Renderer, MP4Validator

from .store import Store
from .auth import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/content-os", tags=["content-os"])

# Pydantic models for request/response
class CreateChannelRequest(BaseModel):
    channel_name: str
    platforms: List[str] = ["youtube_shorts"]
    niche: str = ""
    target_audience: str = ""
    target_market: str = "Vietnam"
    default_language: str = "vi"
    tone: str = "professional"
    visual_identity: Dict[str, Any] = {}
    default_voice: str = ""
    subtitle_profile: Dict[str, Any] = {}
    content_rules: List[str] = []
    forbidden_topics: List[str] = []
    preferred_formats: List[str] = []
    publishing_notes: str = ""

class CreateProjectRequest(BaseModel):
    channel_id: Optional[int] = None
    channel_name: str
    mode: str = "ai_video"
    topic: str
    objective: str = ""
    target_platform: str = "youtube_shorts"
    target_duration_seconds: int = 45
    target_language: str = "vi"
    content_style: str = "trend_decode"
    visual_style: str = "modern_documentary"
    voice_id: str = ""
    subtitle_style_id: str = ""
    background_music_enabled: bool = True
    user_instructions: str = ""


class CreateRunRequest(BaseModel):
    project_id: int


class SubmitApprovalRequest(BaseModel):
    approval_type: str = "script"
    decision: str = "approved"  # approved or rejected
    note: str = ""


class CreateJobRequest(BaseModel):
    source_url: Optional[str] = None


class StoryboardSceneRequest(BaseModel):
    scene_id: str
    order: int
    start_second: float
    end_second: float
    visual_instruction: str
    subtitle_text: str
    narration_text: str
    camera_angle: str = "front"
    transition: str = "cut"
    notes: str = ""
    assets: List[str] = []


class UpdateSceneRequest(BaseModel):
    updates: Dict[str, Any]


class ApproveStoryboardRequest(BaseModel):
    approver_id: int
    notes: str = ""


class RejectStoryboardRequest(BaseModel):
    approver_id: int
    reason: str


class ResolveAssetRequest(BaseModel):
    asset_type: str
    description: str
    preferred_sources: Optional[List[str]] = None


class ValidateMP4Request(BaseModel):
    file_path: str
    expected_duration: float
    expected_resolution: str


class ProjectResponse(BaseModel):
    id: int
    user_id: int
    channel_id: Optional[int]
    channel_name: str
    mode: str
    topic: str
    objective: Optional[str] = None
    target_platform: str
    target_duration_seconds: int
    target_language: str
    content_style: Optional[str] = None
    visual_style: Optional[str] = None
    voice_id: Optional[str] = None
    subtitle_style_id: Optional[str] = None
    background_music_enabled: bool
    user_instructions: Optional[str] = None
    settings: Dict[str, Any]
    created_at: float
    updated_at: float


class RunResponse(BaseModel):
    id: int
    project_id: int
    user_id: int
    workflow_version: str
    status: str
    current_stage: str
    progress_percent: int
    revision_count: int
    warning_json: Optional[str]
    error_json: Optional[str]
    created_at: float
    started_at: Optional[float]
    completed_at: Optional[float]
    updated_at: float


# Dependency to get Content OS components
def get_content_os_components(user_id: int):
    """Get Content OS repository, artifact store, and workflow."""
    if not CONTENT_OS_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Content OS feature is disabled"
        )
    
    db_path = Path(os.environ.get("WEB_DB_PATH", TEMP_DIR / "database.sqlite3"))
    store = Store(db_path=db_path)
    repo = ContentOSRepository(db_path)
    artifact_store = ArtifactStore(base_dir=CONTENT_OS_ARTIFACT_DIR)
    
    workflow = ContentOSWorkflow(
        repository=repo,
        artifact_store=artifact_store,
        config=WorkflowConfig(auto_approve=False, max_revision_attempts=3),
    )
    
    adapter = PipelineAdapter(
        repository=repo,
        artifact_store=artifact_store,
        web_store_db_path=db_path,
    )
    
    return repo, artifact_store, workflow, adapter


@router.get("/health")
async def health_check():
    """Check if Content OS is enabled and available."""
    return {
        "enabled": CONTENT_OS_ENABLED,
        "llm_provider": CONTENT_OS_LLM_PROVIDER,
        "llm_model": CONTENT_OS_LLM_MODEL,
    }


# ==================== Channels ====================

@router.post("/channels")
async def create_channel(
    request: CreateChannelRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Create a new Content OS channel."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    channel = repo.create_channel(
        user_id=user_id,
        channel_name=request.channel_name,
        platforms=request.platforms,
        niche=request.niche,
        target_audience=request.target_audience,
        target_market=request.target_market,
        default_language=request.default_language,
        tone=request.tone,
        visual_identity=request.visual_identity,
        default_voice=request.default_voice,
        subtitle_profile=request.subtitle_profile,
        content_rules=request.content_rules,
        forbidden_topics=request.forbidden_topics,
        preferred_formats=request.preferred_formats,
        publishing_notes=request.publishing_notes,
    )
    
    return {
        "id": channel.id,
        "channel_name": channel.channel_name,
        "platforms": channel.platforms,
        "niche": channel.niche,
        "target_audience": channel.target_audience,
        "target_market": channel.target_market,
        "default_language": channel.default_language,
        "tone": channel.tone,
        "created_at": channel.created_at,
    }

@router.get("/channels")
async def list_channels(
    user_id: int = Depends(get_current_user_id),
):
    """List all channels for the current user."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    channels = repo.list_channels(user_id=user_id, active_only=True)
    
    return [
        {
            "id": channel.id,
            "channel_name": channel.channel_name,
            "platforms": channel.platforms,
            "niche": channel.niche,
            "target_audience": channel.target_audience,
            "target_market": channel.target_market,
            "default_language": channel.default_language,
            "tone": channel.tone,
            "active": channel.active,
            "created_at": channel.created_at,
        }
        for channel in channels
    ]

@router.get("/channels/{channel_id}")
async def get_channel(
    channel_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get a specific channel."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    channel = repo.get_channel(channel_id, user_id=user_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    return {
        "id": channel.id,
        "channel_name": channel.channel_name,
        "platforms": channel.platforms,
        "niche": channel.niche,
        "target_audience": channel.target_audience,
        "target_market": channel.target_market,
        "default_language": channel.default_language,
        "tone": channel.tone,
        "visual_identity": channel.visual_identity,
        "default_voice": channel.default_voice,
        "subtitle_profile": channel.subtitle_profile,
        "content_rules": channel.content_rules,
        "forbidden_topics": channel.forbidden_topics,
        "preferred_formats": channel.preferred_formats,
        "publishing_notes": channel.publishing_notes,
        "active": channel.active,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }

@router.patch("/channels/{channel_id}")
async def update_channel(
    channel_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
):
    """Update a channel."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    channel = repo.update_channel(channel_id, user_id=user_id, **request)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    return {"message": "Channel updated successfully"}

@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Delete a channel (soft delete)."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    success = repo.delete_channel(channel_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    return {"message": "Channel deleted successfully"}


# ==================== Projects ====================

@router.post("/projects", response_model=ProjectResponse)
async def create_project(
    request: CreateProjectRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Create a new Content OS project."""
    repo, _, _, _ = get_content_os_components(user_id)
    
    project = repo.create_project(
        user_id=user_id,
        channel_id=request.channel_id,
        channel_name=request.channel_name,
        mode=request.mode,
        topic=request.topic,
        objective=request.objective,
        target_platform=request.target_platform,
        target_duration_seconds=request.target_duration_seconds,
        target_language=request.target_language,
        content_style=request.content_style,
        visual_style=request.visual_style,
        voice_id=request.voice_id,
        subtitle_style_id=request.subtitle_style_id,
        background_music_enabled=request.background_music_enabled,
        user_instructions=request.user_instructions,
    )
    
    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        channel_id=project.channel_id,
        channel_name=project.channel_name,
        mode=project.mode,
        topic=project.topic,
        objective=project.objective,
        target_platform=project.target_platform,
        target_duration_seconds=project.target_duration_seconds,
        target_language=project.target_language,
        content_style=project.content_style,
        visual_style=project.visual_style,
        voice_id=project.voice_id,
        subtitle_style_id=project.subtitle_style_id,
        background_music_enabled=project.background_music_enabled,
        user_instructions=project.user_instructions,
        settings=project.settings,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects(
    user_id: int = Depends(get_current_user_id),
):
    """List all projects for the current user."""
    repo, _, _, _ = get_content_os_components(user_id)
    
    projects = repo.list_projects(user_id=user_id)
    
    return [
        ProjectResponse(
            id=p.id,
            user_id=p.user_id,
            channel_id=p.channel_id,
            channel_name=p.channel_name,
            mode=p.mode,
            topic=p.topic,
            objective=p.objective,
            target_platform=p.target_platform,
            target_duration_seconds=p.target_duration_seconds,
            target_language=p.target_language,
            content_style=p.content_style,
            visual_style=p.visual_style,
            voice_id=p.voice_id,
            subtitle_style_id=p.subtitle_style_id,
            background_music_enabled=p.background_music_enabled,
            user_instructions=p.user_instructions,
            settings=p.settings,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in projects
    ]


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get a specific project."""
    repo, _, _, _ = get_content_os_components(user_id)
    
    project = repo.get_project(project_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        channel_id=project.channel_id,
        channel_name=project.channel_name,
        mode=project.mode,
        topic=project.topic,
        objective=project.objective,
        target_platform=project.target_platform,
        target_duration_seconds=project.target_duration_seconds,
        target_language=project.target_language,
        content_style=project.content_style,
        visual_style=project.visual_style,
        voice_id=project.voice_id,
        subtitle_style_id=project.subtitle_style_id,
        background_music_enabled=project.background_music_enabled,
        user_instructions=project.user_instructions,
        settings=project.settings,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Delete a project and all its associated data."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    try:
        success = repo.delete_project(project_id, user_id)
        if not success:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"message": "Project deleted successfully"}
    except Exception as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/artifacts/{artifact_type}")
async def get_run_artifact(
    run_id: int,
    artifact_type: str,
    user_id: int = Depends(get_current_user_id),
):
    """Get a specific artifact from a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, artifact_store, _, _ = get_content_os_components(user_id)
    
    try:
        # Get the run to verify it belongs to the user
        run = repo.get_run(run_id, user_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        logger.info(f"Getting artifact {artifact_type} for run {run_id}, user {user_id}, project {run.project_id}")
        
        # Read the artifact data from file (using the 'read' method)
        from universal_video_ai.content_os.enums import ArtifactType
        artifact_data = artifact_store.read(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType(artifact_type),
            version=None,  # Get latest version
        )
        
        logger.info(f"Artifact data type: {type(artifact_data)}")
        if isinstance(artifact_data, dict):
            logger.info(f"Artifact data keys: {list(artifact_data.keys())}")
            logger.info(f"Artifact data preview: {str(artifact_data)[:1000]}")
        else:
            logger.info(f"Artifact data is not dict: {str(artifact_data)[:500]}")
        
        return artifact_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get artifact {artifact_type} for run {run_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs", response_model=RunResponse)
async def create_run(
    request: CreateRunRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Create a new run for a project."""
    repo, _, _, _ = get_content_os_components(user_id)
    
    run = repo.create_run(project_id=request.project_id, user_id=user_id)
    
    return RunResponse(
        id=run.id,
        project_id=run.project_id,
        user_id=run.user_id,
        workflow_version=run.workflow_version,
        status=run.status,
        current_stage=run.current_stage,
        progress_percent=run.progress_percent,
        revision_count=run.revision_count,
        warning_json=run.warning_json,
        error_json=run.error_json,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        updated_at=run.updated_at,
    )


@router.get("/runs", response_model=List[RunResponse])
async def list_runs(
    project_id: Optional[int] = None,
    user_id: int = Depends(get_current_user_id),
):
    """List runs, optionally filtered by project."""
    repo, _, _, _ = get_content_os_components(user_id)
    
    if project_id:
        runs = repo.list_runs(project_id=project_id, user_id=user_id)
    else:
        runs = repo.list_runs(user_id=user_id)
    
    return [
        RunResponse(
            id=r.id,
            project_id=r.project_id,
            user_id=r.user_id,
            workflow_version=r.workflow_version,
            status=r.status,
            current_stage=r.current_stage,
            progress_percent=r.progress_percent,
            revision_count=r.revision_count,
            warning_json=r.warning_json,
            error_json=r.error_json,
            created_at=r.created_at,
            started_at=r.started_at,
            completed_at=r.completed_at,
            updated_at=r.updated_at,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get a specific run."""
    repo, _, _, _ = get_content_os_components(user_id)
    
    run = repo.get_run(run_id, user_id=user_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    
    return RunResponse(
        id=run.id,
        project_id=run.project_id,
        user_id=run.user_id,
        workflow_version=run.workflow_version,
        status=run.status,
        current_stage=run.current_stage,
        progress_percent=run.progress_percent,
        revision_count=run.revision_count,
        warning_json=run.warning_json,
        error_json=run.error_json,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        updated_at=run.updated_at,
    )


@router.post("/runs/{run_id}/start")
async def start_run(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Start executing a workflow run."""
    _, _, workflow, _ = get_content_os_components(user_id)
    
    try:
        result = workflow.start_run(run_id, user_id=user_id)
        return {"status": "started", "run_id": run_id, "result": result}
    except Exception as e:
        logger.error(f"Failed to start run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Cancel a running workflow run."""
    _, _, workflow, _ = get_content_os_components(user_id)
    
    try:
        workflow.cancel_run(run_id, user_id=user_id)
        return {"status": "cancelled", "run_id": run_id}
    except Exception as e:
        logger.error(f"Failed to cancel run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/approve")
async def submit_approval(
    run_id: int,
    request: SubmitApprovalRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Submit an approval decision for a run."""
    _, _, workflow, _ = get_content_os_components(user_id)
    
    try:
        approval_type = ApprovalType(request.approval_type)
        result = workflow.submit_approval(
            run_id=run_id,
            user_id=user_id,
            approval_type=approval_type,
            decision=request.decision,
            note=request.note,
        )
        return {"status": "approved" if request.decision == "approved" else "rejected", "run_id": run_id, "result": result}
    except Exception as e:
        logger.error(f"Failed to submit approval for run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/create-job")
async def create_localization_job(
    run_id: int,
    request: CreateJobRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Create a localization job from an approved run."""
    _, _, workflow, adapter = get_content_os_components(user_id)
    
    try:
        job_id = adapter.create_job_from_run(
            run_id=run_id,
            user_id=user_id,
            source_url=request.source_url,
        )
        
        # Trigger production pipeline to generate actual video
        # Get the script and content plan artifacts
        script_artifact = workflow.artifact_store.read(
            user_id=user_id,
            project_id=workflow.repository.get_run(run_id, user_id).project_id,
            run_id=run_id,
            artifact_type=ArtifactType.SCRIPT,
        )
        plan_artifact = workflow.artifact_store.read(
            user_id=user_id,
            project_id=workflow.repository.get_run(run_id, user_id).project_id,
            run_id=run_id,
            artifact_type=ArtifactType.CONTENT_PLAN,
        )
        
        if script_artifact:
            context = {
                "script": script_artifact.get("data", script_artifact),
                "content_plan": plan_artifact.get("data", plan_artifact) if plan_artifact else {},
            }
            
            # Execute production stages in background
            import threading
            def run_production():
                job_store = Store(adapter.web_store_db_path)
                try:
                    job_store.update_job(
                        job_id,
                        status="running",
                        progress_note="Content OS đang dựng video...",
                    )
                    workflow.repository.update_run(
                        run_id=run_id,
                        user_id=user_id,
                        status="running",
                        current_stage="ready_for_localization",
                        progress_percent=workflow._calculate_progress(WorkflowStage.READY_FOR_LOCALIZATION),
                        error_json=None,
                        completed_at=None,
                    )
                    result = workflow._execute_production_stages(run_id, user_id, context)
                    output_path = result.get("output_path")
                    if not output_path or not Path(output_path).exists():
                        raise RuntimeError(f"Render finished without output file: {output_path}")
                    job_store.update_job(
                        job_id,
                        final_video_path=output_path,
                        status="done",
                        progress_note="Hoàn tất",
                    )
                    logger.info(f"Updated job {job_id} with video path: {output_path}")
                except Exception as e:
                    logger.error(f"Production pipeline failed for run {run_id}: {e}")
                    job_store.update_job(
                        job_id,
                        status="error",
                        error=str(e),
                        progress_note="Content OS render lỗi",
                    )
            
            # Start production in background thread
            thread = threading.Thread(target=run_production, daemon=True)
            thread.start()
            logger.info(f"Started production pipeline for run {run_id} in background")
        
        return {"job_id": job_id, "run_id": run_id, "status": "production_started"}
    except Exception as e:
        logger.error(f"Failed to create job from run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Storyboard ====================

@router.get("/runs/{run_id}/storyboard")
async def get_storyboard(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get storyboard for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    storyboard = storyboard_manager._get_storyboard(run_id, user_id)
    if not storyboard:
        raise HTTPException(status_code=404, detail="Storyboard not found")
    
    return {
        "run_id": storyboard.run_id,
        "user_id": storyboard.user_id,
        "version": storyboard.version,
        "status": storyboard.status.value,
        "scenes": [
            {
                "scene_id": s.scene_id,
                "order": s.order,
                "start_second": s.start_second,
                "end_second": s.end_second,
                "visual_instruction": s.visual_instruction,
                "subtitle_text": s.subtitle_text,
                "narration_text": s.narration_text,
                "camera_angle": s.camera_angle,
                "transition": s.transition,
                "notes": s.notes,
                "assets": s.assets,
            }
            for s in storyboard.scenes
        ],
        "total_duration": storyboard.total_duration,
        "created_at": storyboard.created_at,
        "updated_at": storyboard.updated_at,
        "metadata": storyboard.metadata,
    }


@router.patch("/runs/{run_id}/storyboard/scenes/{scene_id}")
async def update_storyboard_scene(
    run_id: int,
    scene_id: str,
    request: UpdateSceneRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Update a scene in the storyboard."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    storyboard = storyboard_manager.update_scene(
        run_id=run_id,
        user_id=user_id,
        scene_id=scene_id,
        updates=request.updates,
    )
    
    return {"message": "Scene updated successfully", "version": storyboard.version}


@router.post("/runs/{run_id}/storyboard/scenes")
async def add_storyboard_scene(
    run_id: int,
    request: StoryboardSceneRequest,
    after_scene_id: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """Add a new scene to the storyboard."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    from universal_video_ai.content_os.storyboard import StoryboardScene
    scene = StoryboardScene(
        scene_id=request.scene_id,
        order=request.order,
        start_second=request.start_second,
        end_second=request.end_second,
        visual_instruction=request.visual_instruction,
        subtitle_text=request.subtitle_text,
        narration_text=request.narration_text,
        camera_angle=request.camera_angle,
        transition=request.transition,
        notes=request.notes,
        assets=request.assets,
    )
    
    storyboard = storyboard_manager.add_scene(
        run_id=run_id,
        user_id=user_id,
        scene=scene,
        after_scene_id=after_scene_id,
    )
    
    return {"message": "Scene added successfully", "version": storyboard.version}


@router.delete("/runs/{run_id}/storyboard/scenes/{scene_id}")
async def delete_storyboard_scene(
    run_id: int,
    scene_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """Delete a scene from the storyboard."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    storyboard = storyboard_manager.delete_scene(
        run_id=run_id,
        user_id=user_id,
        scene_id=scene_id,
    )
    
    return {"message": "Scene deleted successfully", "version": storyboard.version}


@router.post("/runs/{run_id}/storyboard/submit")
async def submit_storyboard_for_review(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Submit storyboard for review."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    storyboard = storyboard_manager.submit_for_review(
        run_id=run_id,
        user_id=user_id,
    )
    
    return {"message": "Storyboard submitted for review", "status": storyboard.status.value}


@router.post("/runs/{run_id}/storyboard/approve")
async def approve_storyboard(
    run_id: int,
    request: ApproveStoryboardRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Approve the storyboard."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    storyboard = storyboard_manager.approve_storyboard(
        run_id=run_id,
        user_id=user_id,
        approver_id=request.approver_id,
        notes=request.notes,
    )
    
    return {"message": "Storyboard approved", "status": storyboard.status.value}


@router.post("/runs/{run_id}/storyboard/reject")
async def reject_storyboard(
    run_id: int,
    request: RejectStoryboardRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Reject the storyboard."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    storyboard_manager = StoryboardManager(repo)
    
    storyboard = storyboard_manager.reject_storyboard(
        run_id=run_id,
        user_id=user_id,
        approver_id=request.approver_id,
        reason=request.reason,
    )
    
    return {"message": "Storyboard rejected", "status": storyboard.status.value}


# ==================== Asset Resolver ====================

@router.post("/runs/{run_id}/assets/resolve")
async def resolve_asset(
    run_id: int,
    request: ResolveAssetRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Resolve an asset for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    asset_resolver = AssetResolver(repo)
    
    from universal_video_ai.content_os.asset_resolver import AssetType
    asset = asset_resolver.resolve_asset(
        run_id=run_id,
        user_id=user_id,
        asset_type=AssetType(request.asset_type),
        description=request.description,
        preferred_sources=request.preferred_sources,
    )
    
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    return {
        "asset_id": asset.asset_id,
        "asset_type": asset.asset_type.value,
        "source": asset.source.value,
        "url": asset.url,
        "local_path": asset.local_path,
        "metadata": asset.metadata,
        "license_info": asset.license_info,
        "duration_seconds": asset.duration_seconds,
        "resolution": asset.resolution,
        "file_size_bytes": asset.file_size_bytes,
    }


@router.get("/runs/{run_id}/assets/manifest")
async def get_asset_manifest(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get asset manifest for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    asset_resolver = AssetResolver(repo)
    
    manifest = asset_resolver.get_manifest(run_id, user_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Asset manifest not found")
    
    return {
        "run_id": manifest.run_id,
        "user_id": manifest.user_id,
        "assets": [
            {
                "asset_id": a.asset_id,
                "asset_type": a.asset_type.value,
                "source": a.source.value,
                "url": a.url,
                "local_path": a.local_path,
                "metadata": a.metadata,
                "license_info": a.license_info,
                "duration_seconds": a.duration_seconds,
                "resolution": a.resolution,
                "file_size_bytes": a.file_size_bytes,
            }
            for a in manifest.assets
        ],
        "total_size_bytes": manifest.total_size_bytes,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
    }


# ==================== Voice Generation (TTS) ====================

@router.post("/runs/{run_id}/voice/generate")
async def generate_voice(
    run_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
):
    """Generate TTS audio for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, artifact_store, _, _ = get_content_os_components(user_id)
    
    from universal_video_ai.content_os.adapters import TTSAdapter
    tts_adapter = TTSAdapter()
    
    text = request.get("text", "")
    language = request.get("language", "vi")
    voice_id = request.get("voice_id", "")
    
    output_dir = artifact_store._get_run_dir(user_id, 0, run_id)  # project_id not available here
    
    try:
        audio_path = tts_adapter.generate_audio(
            text=text,
            language=language,
            voice_id=voice_id,
            output_dir=output_dir,
        )
        
        return {
            "audio_path": str(audio_path),
            "language": language,
            "voice_id": voice_id,
            "status": "completed",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")


# ==================== Subtitle Generation ====================

@router.post("/runs/{run_id}/subtitles/generate")
async def generate_subtitles(
    run_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
):
    """Generate subtitles for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, artifact_store, _, _ = get_content_os_components(user_id)
    
    from universal_video_ai.content_os.adapters import SubtitleAdapter
    subtitle_adapter = SubtitleAdapter()
    
    segments = request.get("segments", [])
    duration = request.get("duration", 30.0)
    
    output_dir = artifact_store._get_run_dir(user_id, 0, run_id)
    
    try:
        subtitle_path = subtitle_adapter.generate_subtitles(
            segments=segments,
            duration=duration,
            output_dir=output_dir,
        )
        
        return {
            "subtitle_path": str(subtitle_path),
            "format": "srt",
            "segments_count": len(segments),
            "duration_seconds": duration,
            "status": "completed",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Subtitle generation failed: {str(e)}")


# ==================== Timeline Building ====================

@router.post("/runs/{run_id}/timeline/build")
async def build_timeline(
    run_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
):
    """Build timeline for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    from universal_video_ai.content_os.adapters import TimelineAdapter
    timeline_adapter = TimelineAdapter()
    
    script = request.get("script", {})
    voice_manifest = request.get("voice_manifest", {})
    subtitle_manifest = request.get("subtitle_manifest", {})
    assets = request.get("assets", {})
    target_platform = request.get("target_platform", "youtube_shorts")
    target_duration = request.get("target_duration", 30.0)
    
    try:
        timeline = timeline_adapter.build_timeline(
            script=script,
            voice_manifest=voice_manifest,
            subtitle_manifest=subtitle_manifest,
            assets=assets,
            target_platform=target_platform,
            target_duration=target_duration,
        )
        
        return {
            "timeline": timeline,
            "status": "completed",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Timeline building failed: {str(e)}")


# ==================== Output Streaming ====================

@router.get("/runs/{run_id}/output/download")
async def download_output(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Download the final MP4 output for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    from fastapi.responses import FileResponse
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    # Get the latest render artifact
    artifacts = repo.list_artifacts(run_id)
    render_artifact = None
    for artifact in artifacts:
        if artifact.artifact_type == "render_report":
            render_artifact = artifact
            break
    
    if not render_artifact:
        raise HTTPException(status_code=404, detail="Render output not found")
    
    output_path = render_artifact.metadata.get("output_path") if hasattr(render_artifact, 'metadata') else None
    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    
    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename=f"content_os_run_{run_id}.mp4",
    )


@router.get("/runs/{run_id}/output/stream")
async def stream_output(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Stream the final MP4 output for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    from fastapi.responses import FileResponse
    
    repo, _, _, _ = get_content_os_components(user_id)
    
    # Get the latest render artifact
    artifacts = repo.list_artifacts(run_id)
    render_artifact = None
    for artifact in artifacts:
        if artifact.artifact_type == "render_report":
            render_artifact = artifact
            break
    
    if not render_artifact:
        raise HTTPException(status_code=404, detail="Render output not found")
    
    output_path = render_artifact.metadata.get("output_path") if hasattr(render_artifact, 'metadata') else None
    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    
    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename=f"content_os_run_{run_id}.mp4",
    )


# ==================== Renderer ====================

@router.post("/runs/{run_id}/render")
async def submit_render_job(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Submit a render job for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    renderer = Renderer(repo)
    
    job = renderer.submit_render_job(
        run_id=run_id,
        user_id=user_id,
        timeline_path=f"/timelines/{run_id}.json",
        output_path=f"/output/{run_id}.mp4",
    )
    
    return {
        "job_id": job.job_id,
        "run_id": job.run_id,
        "status": job.status.value,
        "timeline_path": job.timeline_path,
        "output_path": job.output_path,
    }


@router.post("/runs/{run_id}/render/start")
async def start_render(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Start the render job for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    renderer = Renderer(repo)
    
    job = renderer.get_render_job(run_id, user_id)
    if not job:
        raise HTTPException(status_code=404, detail="Render job not found")
    
    updated_job = renderer.start_render(job)
    
    return {
        "job_id": updated_job.job_id,
        "status": updated_job.status.value,
        "progress": updated_job.progress,
        "completed_at": updated_job.completed_at,
    }


@router.get("/runs/{run_id}/render/status")
async def get_render_status(
    run_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get render job status for a run."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    repo, _, _, _ = get_content_os_components(user_id)
    renderer = Renderer(repo)
    
    job = renderer.get_render_job(run_id, user_id)
    if not job:
        raise HTTPException(status_code=404, detail="Render job not found")
    
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "progress": job.progress,
        "timeline_path": job.timeline_path,
        "output_path": job.output_path,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


@router.post("/validate-mp4")
async def validate_mp4(
    request: ValidateMP4Request,
    user_id: int = Depends(get_current_user_id),
):
    """Validate an MP4 file."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    validator = MP4Validator()
    
    result = validator.validate(
        file_path=request.file_path,
        expected_duration=request.expected_duration,
        expected_resolution=request.expected_resolution,
    )
    
    return {
        "status": result.status.value,
        "file_path": result.file_path,
        "file_size_bytes": result.file_size_bytes,
        "duration_seconds": result.duration_seconds,
        "resolution": result.resolution,
        "video_codec": result.video_codec,
        "audio_codec": result.audio_codec,
        "bitrate": result.bitrate,
        "issues": result.issues,
        "warnings": result.warnings,
    }


@router.post("/validate-mp4/platform/{platform}")
async def validate_mp4_for_platform(
    platform: str,
    request: ValidateMP4Request,
    user_id: int = Depends(get_current_user_id),
):
    """Validate an MP4 file for a specific platform."""
    if not CONTENT_OS_ENABLED:
        raise FeatureDisabledError("Content OS is disabled")
    
    validator = MP4Validator()
    
    result = validator.validate(
        file_path=request.file_path,
        expected_duration=request.expected_duration,
        expected_resolution=request.expected_resolution,
    )
    
    is_valid = validator.is_valid_for_platform(result, platform)
    
    return {
        "status": result.status.value,
        "platform": platform,
        "is_valid_for_platform": is_valid,
        "file_path": result.file_path,
        "duration_seconds": result.duration_seconds,
        "resolution": result.resolution,
        "issues": result.issues,
        "warnings": result.warnings,
    }
