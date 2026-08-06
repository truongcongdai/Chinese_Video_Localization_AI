"""
Content OS Pydantic schemas.

Request/response models for API endpoints and internal data structures.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator
from .enums import (
    WorkflowStage,
    RunStatus,
    StepStatus,
    RiskLevel,
    AuditDecision,
    ApprovalType,
    MemoryType,
    ArtifactType,
)


# ==================== Project Schemas ====================

class CreateProjectRequest(BaseModel):
    """Request to create a Content OS project."""
    channel_name: str = Field(..., min_length=1, max_length=200)
    target_platforms: List[str] = Field(..., min_length=1)
    topic: str = Field(..., min_length=1, max_length=500)
    target_market: str = Field(default="Vietnam", max_length=100)
    target_language: str = Field(default="vi", min_length=2, max_length=10)
    target_duration_seconds: int = Field(default=45, ge=10, le=300)
    content_format: str = Field(default="trend_decode", max_length=50)
    source_platforms: List[str] = Field(..., min_length=1)
    max_source_items: int = Field(default=10, ge=1, le=100)
    user_instructions: str = Field(default="", max_length=2000)
    auto_download_sources: bool = Field(default=False)
    branding_config: Optional[Dict[str, Any]] = None
    
    @field_validator('target_platforms')
    @classmethod
    def validate_target_platforms(cls, v):
        allowed = {"youtube_shorts", "facebook_reels", "tiktok", "instagram_reels"}
        invalid = [p for p in v if p.lower() not in allowed]
        if invalid:
            raise ValueError(f"Invalid target platforms: {invalid}")
        return [p.lower() for p in v]
    
    @field_validator('source_platforms')
    @classmethod
    def validate_source_platforms(cls, v):
        allowed = {"youtube", "douyin", "kuaishou", "tiktok"}
        invalid = [p for p in v if p.lower() not in allowed]
        if invalid:
            raise ValueError(f"Invalid source platforms: {invalid}")
        return [p.lower() for p in v]
    
    @field_validator('max_source_items')
    @classmethod
    def validate_max_sources(cls, v):
        from universal_video_ai.config import CONTENT_OS_MAX_SOURCE_ITEMS
        if v > CONTENT_OS_MAX_SOURCE_ITEMS:
            raise ValueError(f"max_source_items cannot exceed {CONTENT_OS_MAX_SOURCE_ITEMS}")
        return v


class ProjectResponse(BaseModel):
    """Response with project details."""
    id: int
    user_id: int
    channel_name: str
    topic: str
    target_platforms: List[str]
    source_platforms: List[str]
    target_market: str
    target_language: str
    target_duration_seconds: int
    content_format: str
    max_source_items: int
    user_instructions: str
    auto_download_sources: bool
    status: str
    created_at: datetime
    updated_at: datetime


class UpdateProjectRequest(BaseModel):
    """Request to update a project."""
    channel_name: Optional[str] = Field(None, min_length=1, max_length=200)
    topic: Optional[str] = Field(None, min_length=1, max_length=500)
    target_platforms: Optional[List[str]] = None
    source_platforms: Optional[List[str]] = None
    target_market: Optional[str] = Field(None, max_length=100)
    target_language: Optional[str] = Field(None, min_length=2, max_length=10)
    target_duration_seconds: Optional[int] = Field(None, ge=10, le=300)
    content_format: Optional[str] = Field(None, max_length=50)
    max_source_items: Optional[int] = Field(None, ge=1, le=100)
    user_instructions: Optional[str] = Field(None, max_length=2000)
    auto_download_sources: Optional[bool] = None


# ==================== Run Schemas ====================

class CreateRunRequest(BaseModel):
    """Request to create a run from a project."""
    project_id: int


class RunResponse(BaseModel):
    """Response with run details."""
    id: int
    project_id: int
    user_id: int
    workflow_version: str
    status: str
    current_stage: str
    progress_percent: int
    revision_count: int
    warning: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime


class StepResponse(BaseModel):
    """Response with step details."""
    id: int
    run_id: int
    stage: str
    agent_name: str
    status: str
    input_artifact_ids: List[int]
    output_artifact_ids: List[int]
    attempt: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[Dict[str, Any]] = None


# ==================== Agent Output Schemas ====================

class TrendCandidate(BaseModel):
    """A single trend candidate from trend research."""
    title: str
    platform: str
    source_url: str
    author: Optional[str] = None
    published_at: Optional[str] = None
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    trend_score: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


class TrendRadarResult(BaseModel):
    """Output from TrendRadarAgent."""
    topic: str
    expanded_keywords: List[str] = Field(default_factory=list)
    detected_trends: List[TrendCandidate] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class SourceAnalysisItem(BaseModel):
    """Analysis result for a single source."""
    source_id: str
    source_url: str
    platform: str
    title: str
    relevance_score: float = 0.0
    visual_quality_score: float = 0.0
    content_value_score: float = 0.0
    reuse_risk: RiskLevel = RiskLevel.MEDIUM
    copyright_risk: RiskLevel = RiskLevel.MEDIUM
    download_available: bool = True
    summary: str = ""
    key_claims: List[str] = Field(default_factory=list)
    key_visuals: List[str] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)


class SourceAnalysisResult(BaseModel):
    """Output from SourceAnalyzerAgent."""
    selected_sources: List[SourceAnalysisItem] = Field(default_factory=list)
    rejected_sources: List[SourceAnalysisItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ContentBeat(BaseModel):
    """A single beat in the content plan."""
    order: int = Field(..., ge=1)
    start_second: float = Field(..., ge=0.0)
    end_second: float = Field(..., ge=0.0)
    purpose: str = ""
    narration_goal: str = ""
    visual_goal: str = ""


class ContentPlan(BaseModel):
    """Output from ContentPlannerAgent."""
    content_angle: str = ""
    target_platforms: List[str] = Field(default_factory=list)
    target_duration_seconds: int = 45
    target_audience: str = ""
    core_message: str = ""
    hook: str = ""
    beats: List[ContentBeat] = Field(default_factory=list)
    must_include: List[str] = Field(default_factory=list)
    must_avoid: List[str] = Field(default_factory=list)
    source_usage_plan: List[str] = Field(default_factory=list)
    original_value_add: List[str] = Field(default_factory=list)
    call_to_action: str = ""


class ScriptSegment(BaseModel):
    """A single segment in the generated script."""
    segment_id: str = Field(..., min_length=1)
    start_second: float = Field(..., ge=0.0)
    end_second: float = Field(..., ge=0.0)
    narration: str = ""
    subtitle_text: str = ""
    visual_instruction: str = ""
    source_refs: List[str] = Field(default_factory=list)


class GeneratedScript(BaseModel):
    """Output from ScriptWriterAgent."""
    title_options: List[str] = Field(default_factory=list)
    hook: str = ""
    narration_text: str = ""
    segments: List[ScriptSegment] = Field(default_factory=list)
    description: str = ""
    hashtags: List[str] = Field(default_factory=list)
    estimated_duration_seconds: float = 0.0
    source_attributions: List[str] = Field(default_factory=list)


class AuditIssue(BaseModel):
    """An issue found during audit."""
    issue_id: str
    severity: Literal["info", "warning", "critical"]
    category: str
    segment_id: Optional[str] = None
    description: str
    required_fix: str = ""


class AuditResult(BaseModel):
    """Output from ContentAuditAgent."""
    decision: AuditDecision
    overall_score: float = 0.0
    hook_strength: float = 0.0
    originality_score: float = 0.0
    clarity_score: float = 0.0
    retention_score: float = 0.0
    source_dependency: RiskLevel = RiskLevel.MEDIUM
    copyright_risk: RiskLevel = RiskLevel.MEDIUM
    factual_risk: RiskLevel = RiskLevel.MEDIUM
    timing_valid: bool = True
    issues: List[AuditIssue] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RevisionResult(BaseModel):
    """Output from ScriptReviserAgent."""
    revised_script: GeneratedScript
    change_summary: str = ""
    remaining_issues: List[AuditIssue] = Field(default_factory=list)


# ==================== Approval Schemas ====================

class ApproveScriptRequest(BaseModel):
    """Request to approve a script."""
    note: str = Field(default="", max_length=1000)


class RejectScriptRequest(BaseModel):
    """Request to reject a script."""
    note: str = Field(default="", max_length=1000)


class ApprovalResponse(BaseModel):
    """Response after approval/rejection."""
    approval_id: int
    run_id: int
    user_id: int
    approval_type: ApprovalType
    decision: Literal["approved", "rejected"]
    note: str
    created_at: datetime


# ==================== Artifact Schemas ====================

class ArtifactResponse(BaseModel):
    """Response with artifact details."""
    id: int
    run_id: int
    user_id: int
    artifact_type: ArtifactType
    version: int
    schema_version: str
    path: str
    checksum: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_by_agent: str
    created_at: datetime


# ==================== Source Schemas ====================

class SourceResponse(BaseModel):
    """Response with source candidate details."""
    id: int
    run_id: int
    user_id: int
    platform: str
    provider: str
    source_url: str
    canonical_url: str
    title: str
    author: Optional[str] = None
    thumbnail_url: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    trend_score: float = 0.0
    selected: bool = False
    download_status: str = "not_downloaded"
    local_path: Optional[str] = None
    risk: Dict[str, Any] = Field(default_factory=dict)
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DownloadSourceRequest(BaseModel):
    """Request to download a source."""
    source_id: int


# ==================== Localization Adapter Schema ====================

class CreateLocalizationJobRequest(BaseModel):
    """Request to create a localization job from an approved run."""
    run_id: int
    selected_source_id: Optional[int] = None
    script_artifact_id: int


class LocalizationJobReference(BaseModel):
    """Reference to a created localization job."""
    job_id: str
    run_id: int
    source_url: str
    target_language: str
    created_at: datetime
