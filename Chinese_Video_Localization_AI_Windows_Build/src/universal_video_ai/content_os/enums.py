"""
Content OS enumerations.

Defines workflow states, risk levels, and other enum constants.
"""
from enum import Enum


class WorkflowStage(str, Enum):
    """Workflow stage states."""
    CREATED = "created"
    RESEARCHING = "researching"
    RESEARCH_READY = "research_ready"
    CONTENT_PLANNING = "content_planning"
    PLAN_READY = "plan_ready"
    SCRIPT_WRITING = "script_writing"
    SCRIPT_AUDITING = "script_auditing"
    SCRIPT_REVISING = "script_revising"
    AWAITING_SCRIPT_APPROVAL = "awaiting_script_approval"
    STORYBOARDING = "storyboarding"
    AWAITING_STORYBOARD_APPROVAL = "awaiting_storyboard_approval"
    ASSET_PLANNING = "asset_planning"
    ASSET_RESOLVING = "asset_resolving"
    ASSETS_READY = "assets_ready"
    VOICE_GENERATION = "voice_generation"
    SUBTITLE_GENERATION = "subtitle_generation"
    TIMELINE_BUILDING = "timeline_building"
    RENDERING = "rendering"
    OUTPUT_VALIDATION = "output_validation"
    COMPLETED = "completed"
    
    # Legacy stages for backward compatibility
    TREND_RESEARCH = "trend_research"
    SOURCE_SELECTION = "source_selection"
    SOURCE_ANALYSIS = "source_analysis"
    SCRIPT_AUDIT = "script_audit"
    SCRIPT_REVISION = "script_revision"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    READY_FOR_LOCALIZATION = "ready_for_localization"
    LOCALIZATION_RUNNING = "localization_running"
    RENDERED = "rendered"
    
    # Failure and control states
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


class RunStatus(str, Enum):
    """Overall run status."""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    LOCALIZATION_RUNNING = "localization_running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


class StepStatus(str, Enum):
    """Individual step status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    """Copyright/reuse risk assessment."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AuditDecision(str, Enum):
    """Audit agent decision."""
    PASS = "PASS"
    PASS_WITH_FIXES = "PASS_WITH_FIXES"
    BLOCKED = "BLOCKED"


class ApprovalType(str, Enum):
    """Approval checkpoint types."""
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    RENDER = "render"
    PUBLISH = "publish"


class MemoryType(str, Enum):
    """Channel memory types."""
    CHANNEL_PROFILE = "channel_profile"
    WINNING_TOPIC = "winning_topic"
    WEAK_TOPIC = "weak_topic"
    WINNING_HOOK = "winning_hook"
    WEAK_HOOK = "weak_hook"
    FORMAT_PREFERENCE = "format_preference"
    SUBTITLE_STYLE = "subtitle_style"
    VOICE_PROFILE = "voice_profile"
    COPYRIGHT_INCIDENT = "copyright_incident"
    PUBLISHING_NOTE = "publishing_note"
    USER_PREFERENCE = "user_preference"


class ArtifactType(str, Enum):
    """Artifact types for versioned storage."""
    RESEARCH_REPORT = "research_report"
    TREND_REPORT = "trend_report"
    SELECTED_SOURCES = "selected_sources"
    SOURCE_ANALYSIS = "source_analysis"
    CONTENT_PLAN = "content_plan"
    CONTEXT_TRACE = "context_trace"
    SCRIPT = "script"
    AUDIT_REPORT = "audit_report"
    REVISION_REPORT = "revision_report"
    STORYBOARD = "storyboard"
    ASSET_MANIFEST = "asset_manifest"
    RESOLVED_ASSETS = "resolved_assets"
    VOICE_MANIFEST = "voice_manifest"
    SUBTITLE_MANIFEST = "subtitle_manifest"
    TIMELINE = "timeline"
    RENDER_REQUEST = "render_request"
    RENDER_REPORT = "render_report"
    OUTPUT_VALIDATION = "output_validation"
    PUBLISH_PACKAGE = "publish_package"
    RUN_TRACE = "run_trace"
