"""
Content OS database models.

Internal data models for database persistence (not API schemas).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
import json


@dataclass
class ContentOSChannel:
    """Database model for content_os_channels table."""
    id: Optional[int]
    user_id: int
    channel_name: str
    platforms_json: str
    niche: str
    target_audience: str
    target_market: str
    default_language: str
    tone: str
    visual_identity_json: str
    default_voice: str
    subtitle_profile_json: str
    content_rules_json: str
    forbidden_topics_json: str
    preferred_formats_json: str
    publishing_notes: str
    active: bool
    created_at: float
    updated_at: float
    
    @property
    def platforms(self) -> List[str]:
        return json.loads(self.platforms_json) if self.platforms_json else []
    
    @property
    def visual_identity(self) -> Dict[str, Any]:
        return json.loads(self.visual_identity_json) if self.visual_identity_json else {}
    
    @property
    def subtitle_profile(self) -> Dict[str, Any]:
        return json.loads(self.subtitle_profile_json) if self.subtitle_profile_json else {}
    
    @property
    def content_rules(self) -> List[str]:
        return json.loads(self.content_rules_json) if self.content_rules_json else []
    
    @property
    def forbidden_topics(self) -> List[str]:
        return json.loads(self.forbidden_topics_json) if self.forbidden_topics_json else []
    
    @property
    def preferred_formats(self) -> List[str]:
        return json.loads(self.preferred_formats_json) if self.preferred_formats_json else []


@dataclass
class ContentOSProject:
    """Database model for content_os_projects table."""
    id: Optional[int]
    user_id: int
    channel_id: Optional[int]
    channel_name: str
    mode: str
    topic: str
    objective: str
    target_platform: str
    target_duration_seconds: int
    target_language: str
    content_style: str
    visual_style: str
    voice_id: str
    subtitle_style_id: str
    background_music_enabled: bool
    user_instructions: str
    settings_json: str
    status: str
    created_at: float
    updated_at: float
    
    @property
    def settings(self) -> Dict[str, Any]:
        return json.loads(self.settings_json) if self.settings_json else {}


@dataclass
class ContentOSRun:
    """Database model for content_os_runs table."""
    id: Optional[int]
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
    
    @property
    def warning(self) -> Optional[Dict[str, Any]]:
        return json.loads(self.warning_json) if self.warning_json else None
    
    @property
    def error(self) -> Optional[Dict[str, Any]]:
        return json.loads(self.error_json) if self.error_json else None


@dataclass
class ContentOSStep:
    """Database model for content_os_steps table."""
    id: Optional[int]
    run_id: int
    stage: str
    agent_name: str
    status: str
    input_artifact_ids_json: str
    output_artifact_ids_json: str
    attempt: int
    started_at: Optional[float]
    completed_at: Optional[float]
    error_json: Optional[str]
    created_at: float
    
    @property
    def input_artifact_ids(self) -> List[int]:
        return json.loads(self.input_artifact_ids_json) if self.input_artifact_ids_json else []
    
    @property
    def output_artifact_ids(self) -> List[int]:
        return json.loads(self.output_artifact_ids_json) if self.output_artifact_ids_json else []
    
    @property
    def error(self) -> Optional[Dict[str, Any]]:
        return json.loads(self.error_json) if self.error_json else None


@dataclass
class ContentOSArtifact:
    """Database model for content_os_artifacts table."""
    id: Optional[int]
    run_id: int
    user_id: int
    artifact_type: str
    version: int
    schema_version: str
    path: str
    checksum: str
    metadata_json: str
    created_by_agent: str
    created_at: float
    
    @property
    def metadata(self) -> Dict[str, Any]:
        return json.loads(self.metadata_json) if self.metadata_json else {}


@dataclass
class ContentOSSource:
    """Database model for content_os_sources table."""
    id: Optional[int]
    run_id: int
    user_id: int
    platform: str
    provider: str
    source_url: str
    canonical_url: str
    title: str
    author: Optional[str]
    thumbnail_url: Optional[str]
    metrics_json: str
    trend_score: float
    selected: bool
    download_status: str
    local_path: Optional[str]
    risk_json: str
    raw_json: str
    created_at: float
    updated_at: float
    
    @property
    def metrics(self) -> Dict[str, Any]:
        return json.loads(self.metrics_json) if self.metrics_json else {}
    
    @property
    def risk(self) -> Dict[str, Any]:
        return json.loads(self.risk_json) if self.risk_json else {}
    
    @property
    def raw_metadata(self) -> Dict[str, Any]:
        return json.loads(self.raw_json) if self.raw_json else {}


@dataclass
class ContentOSReview:
    """Database model for content_os_reviews table."""
    id: Optional[int]
    run_id: int
    artifact_id: int
    decision: str
    scores_json: str
    issues_json: str
    created_at: float
    
    @property
    def scores(self) -> Dict[str, Any]:
        return json.loads(self.scores_json) if self.scores_json else {}
    
    @property
    def issues(self) -> List[Dict[str, Any]]:
        return json.loads(self.issues_json) if self.issues_json else []


@dataclass
class ContentOSApproval:
    """Database model for content_os_approvals table."""
    id: Optional[int]
    run_id: int
    user_id: int
    approval_type: str
    decision: str
    note: str
    created_at: float


@dataclass
class ContentOSMemory:
    """Database model for content_os_memories table."""
    id: Optional[int]
    user_id: int
    channel_key: str
    memory_type: str
    memory_key: str
    value_json: str
    confidence: float
    source_run_id: Optional[int]
    active: bool
    created_at: float
    updated_at: float
    
    @property
    def value(self) -> Any:
        return json.loads(self.value_json) if self.value_json else None
