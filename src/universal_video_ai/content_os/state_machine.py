"""
Content OS state machine.

Validates workflow state transitions according to the defined workflow graph.
"""
from typing import Dict, Set, Optional
from .enums import WorkflowStage, RunStatus
from .exceptions import InvalidTransitionError


class StateMachine:
    """
    Validates workflow state transitions.
    
    Workflow:
    CREATED → RESEARCHING → RESEARCH_READY → CONTENT_PLANNING → PLAN_READY → 
    SCRIPT_WRITING → SCRIPT_AUDITING → (SCRIPT_REVISING when required) → 
    AWAITING_SCRIPT_APPROVAL → STORYBOARDING → AWAITING_STORYBOARD_APPROVAL → 
    ASSET_PLANNING → ASSET_RESOLVING → ASSETS_READY → VOICE_GENERATION → 
    SUBTITLE_GENERATION → TIMELINE_BUILDING → RENDERING → OUTPUT_VALIDATION → COMPLETED
    
    Legacy stages for backward compatibility:
    TREND_RESEARCH, SOURCE_SELECTION, SOURCE_ANALYSIS, AWAITING_APPROVAL, 
    APPROVED, READY_FOR_LOCALIZATION, LOCALIZATION_RUNNING, RENDERED
    
    Control states: PAUSED, CANCELLED, FAILED, BLOCKED, INTERRUPTED
    """
    
    # Valid transitions from each stage
    _TRANSITIONS: Dict[WorkflowStage, Set[WorkflowStage]] = {
        WorkflowStage.CREATED: {
            WorkflowStage.RESEARCHING,
            WorkflowStage.TREND_RESEARCH,  # Legacy
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.RESEARCHING: {
            WorkflowStage.RESEARCH_READY,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.RESEARCH_READY: {
            WorkflowStage.CONTENT_PLANNING,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.CONTENT_PLANNING: {
            WorkflowStage.PLAN_READY,
            WorkflowStage.SCRIPT_WRITING,  # Legacy path for backward compatibility
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.PLAN_READY: {
            WorkflowStage.SCRIPT_WRITING,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SCRIPT_WRITING: {
            WorkflowStage.SCRIPT_AUDITING,
            WorkflowStage.SCRIPT_AUDIT,  # Legacy for backward compatibility
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SCRIPT_AUDITING: {
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,  # Pass
            WorkflowStage.SCRIPT_REVISING,           # Needs fixes
            WorkflowStage.BLOCKED,                   # Critical issues
            WorkflowStage.FAILED,                    # Fatal error
        },
        WorkflowStage.SCRIPT_AUDIT: {
            WorkflowStage.AWAITING_APPROVAL,  # Legacy
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,  # New
            WorkflowStage.SCRIPT_REVISION,    # Legacy
            WorkflowStage.SCRIPT_REVISING,     # New
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SCRIPT_REVISING: {
            WorkflowStage.SCRIPT_AUDITING,            # Re-audit after revision
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,   # Max revisions reached
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SCRIPT_REVISION: {
            WorkflowStage.SCRIPT_AUDIT,        # Legacy
            WorkflowStage.SCRIPT_AUDITING,     # New
            WorkflowStage.AWAITING_APPROVAL,  # Legacy
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,  # New
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.AWAITING_SCRIPT_APPROVAL: {
            WorkflowStage.STORYBOARDING,
            WorkflowStage.ASSET_PLANNING,              # Skip storyboard
            WorkflowStage.CANCELLED,
            WorkflowStage.BLOCKED,
        },
        WorkflowStage.STORYBOARDING: {
            WorkflowStage.AWAITING_STORYBOARD_APPROVAL,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.AWAITING_STORYBOARD_APPROVAL: {
            WorkflowStage.ASSET_PLANNING,
            WorkflowStage.CANCELLED,
            WorkflowStage.BLOCKED,
        },
        WorkflowStage.ASSET_PLANNING: {
            WorkflowStage.ASSET_RESOLVING,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.ASSET_RESOLVING: {
            WorkflowStage.ASSETS_READY,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.ASSETS_READY: {
            WorkflowStage.VOICE_GENERATION,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.VOICE_GENERATION: {
            WorkflowStage.SUBTITLE_GENERATION,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SUBTITLE_GENERATION: {
            WorkflowStage.TIMELINE_BUILDING,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.TIMELINE_BUILDING: {
            WorkflowStage.RENDERING,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.RENDERING: {
            WorkflowStage.OUTPUT_VALIDATION,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.OUTPUT_VALIDATION: {
            WorkflowStage.COMPLETED,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.COMPLETED: set(),  # Terminal state
        
        # Legacy stages for backward compatibility
        WorkflowStage.TREND_RESEARCH: {
            WorkflowStage.SOURCE_SELECTION,
            WorkflowStage.RESEARCH_READY,  # New path
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SOURCE_SELECTION: {
            WorkflowStage.SOURCE_ANALYSIS,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SOURCE_ANALYSIS: {
            WorkflowStage.CONTENT_PLANNING,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SCRIPT_AUDIT: {
            WorkflowStage.AWAITING_APPROVAL,  # Legacy
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,  # New
            WorkflowStage.SCRIPT_REVISION,    # Legacy
            WorkflowStage.SCRIPT_REVISING,     # New
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.SCRIPT_REVISION: {
            WorkflowStage.SCRIPT_AUDIT,        # Legacy
            WorkflowStage.SCRIPT_AUDITING,     # New
            WorkflowStage.AWAITING_APPROVAL,  # Legacy
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,  # New
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.AWAITING_APPROVAL: {
            WorkflowStage.APPROVED,            # Legacy
            WorkflowStage.AWAITING_SCRIPT_APPROVAL,  # New
            WorkflowStage.CANCELLED,
            WorkflowStage.BLOCKED,
        },
        WorkflowStage.APPROVED: {
            WorkflowStage.READY_FOR_LOCALIZATION,
            WorkflowStage.CANCELLED,
        },
        WorkflowStage.READY_FOR_LOCALIZATION: {
            WorkflowStage.LOCALIZATION_RUNNING,
            WorkflowStage.STORYBOARDING,  # New production path
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.LOCALIZATION_RUNNING: {
            WorkflowStage.RENDERED,
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.RENDERED: {
            WorkflowStage.COMPLETED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.COMPLETED: set(),  # Terminal state (already defined above)
        
        # Control states can transition to most active stages
        WorkflowStage.PAUSED: {
            WorkflowStage.RESEARCHING,
            WorkflowStage.CONTENT_PLANNING,
            WorkflowStage.SCRIPT_WRITING,
            WorkflowStage.SCRIPT_AUDITING,
            WorkflowStage.SCRIPT_REVISING,
            WorkflowStage.STORYBOARDING,
            WorkflowStage.ASSET_RESOLVING,
            WorkflowStage.VOICE_GENERATION,
            WorkflowStage.SUBTITLE_GENERATION,
            WorkflowStage.TIMELINE_BUILDING,
            WorkflowStage.RENDERING,
            WorkflowStage.OUTPUT_VALIDATION,
            # Legacy
            WorkflowStage.TREND_RESEARCH,
            WorkflowStage.SOURCE_SELECTION,
            WorkflowStage.SOURCE_ANALYSIS,
            WorkflowStage.SCRIPT_AUDIT,
            WorkflowStage.SCRIPT_REVISION,
            WorkflowStage.LOCALIZATION_RUNNING,
        },
        WorkflowStage.CANCELLED: set(),  # Terminal state
        WorkflowStage.FAILED: set(),      # Terminal state
        WorkflowStage.BLOCKED: {
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
        WorkflowStage.INTERRUPTED: {
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
        },
    }
    
    # Stages that require approval record to proceed
    _APPROVAL_REQUIRED: Set[WorkflowStage] = {
        WorkflowStage.AWAITING_APPROVAL,      # Legacy
        WorkflowStage.AWAITING_SCRIPT_APPROVAL,
        WorkflowStage.AWAITING_STORYBOARD_APPROVAL,
    }
    
    # Stages that require artifact validation
    _ARTIFACT_REQUIRED: Dict[WorkflowStage, str] = {
        WorkflowStage.SOURCE_ANALYSIS: "selected_sources",
        WorkflowStage.CONTENT_PLANNING: "source_analysis",
        WorkflowStage.SCRIPT_WRITING: "content_plan",
        WorkflowStage.SCRIPT_AUDIT: "script",
        WorkflowStage.SCRIPT_AUDITING: "script",
        WorkflowStage.SCRIPT_REVISION: "audit_report",
        WorkflowStage.SCRIPT_REVISING: "audit_report",
        WorkflowStage.AWAITING_APPROVAL: "script",      # Legacy
        WorkflowStage.AWAITING_SCRIPT_APPROVAL: "script",
        WorkflowStage.STORYBOARDING: "script",
        WorkflowStage.AWAITING_STORYBOARD_APPROVAL: "storyboard",
        WorkflowStage.ASSET_RESOLVING: "asset_manifest",
        WorkflowStage.VOICE_GENERATION: "voice_manifest",
        WorkflowStage.SUBTITLE_GENERATION: "subtitle_manifest",
        WorkflowStage.TIMELINE_BUILDING: "timeline",
        WorkflowStage.RENDERING: "render_request",
        WorkflowStage.OUTPUT_VALIDATION: "render_report",
        WorkflowStage.READY_FOR_LOCALIZATION: "script",
        WorkflowStage.LOCALIZATION_RUNNING: "script",
        WorkflowStage.RENDERED: "render_report",
        WorkflowStage.COMPLETED: "render_report",
    }
    
    @classmethod
    def validate_transition(
        cls,
        from_stage: WorkflowStage,
        to_stage: WorkflowStage,
        has_approval: bool = False,
        has_required_artifact: bool = True,
    ) -> None:
        """
        Validate a state transition.
        
        Args:
            from_stage: Current stage
            to_stage: Target stage
            has_approval: Whether approval record exists (for approval gates)
            has_required_artifact: Whether required artifact exists
            
        Raises:
            InvalidTransitionError: If transition is invalid
        """
        # Check if transition is allowed
        if to_stage not in cls._TRANSITIONS.get(from_stage, set()):
            raise InvalidTransitionError(
                f"Invalid transition from {from_stage} to {to_stage}. "
                f"Valid transitions from {from_stage}: {cls._TRANSITIONS.get(from_stage, set())}"
            )
        
        # Check approval requirement
        if from_stage in cls._APPROVAL_REQUIRED and not has_approval:
            raise InvalidTransitionError(
                f"Transition from {from_stage} to {to_stage} requires approval record"
            )
        
        # Check artifact requirement
        required_artifact = cls._ARTIFACT_REQUIRED.get(to_stage)
        if required_artifact and not has_required_artifact:
            raise InvalidTransitionError(
                f"Transition to {to_stage} requires artifact of type {required_artifact}"
            )
    
    @classmethod
    def can_resume_from(cls, stage: WorkflowStage) -> WorkflowStage:
        """
        Determine which stage to resume from when resuming from PAUSED.
        Returns the stage that should be executed next.
        """
        # When resuming, go to the next logical stage
        resume_map = {
            WorkflowStage.PAUSED: WorkflowStage.TREND_RESEARCH,  # Default, should be overridden by context
        }
        return resume_map.get(stage, stage)
    
    @classmethod
    def is_terminal(cls, stage: WorkflowStage) -> bool:
        """Check if a stage is terminal (no outgoing transitions)."""
        return not cls._TRANSITIONS.get(stage, set())
    
    @classmethod
    def is_control_state(cls, stage: WorkflowStage) -> bool:
        """Check if a stage is a control state (PAUSED, CANCELLED, FAILED, BLOCKED)."""
        return stage in {
            WorkflowStage.PAUSED,
            WorkflowStage.CANCELLED,
            WorkflowStage.FAILED,
            WorkflowStage.BLOCKED,
        }
    
    @classmethod
    def get_required_artifact_type(cls, stage: WorkflowStage) -> Optional[str]:
        """Get the required artifact type for entering a stage."""
        return cls._ARTIFACT_REQUIRED.get(stage)
