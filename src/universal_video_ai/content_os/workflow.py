"""
Content OS workflow orchestration layer.

Coordinates the entire content creation workflow, managing state transitions,
agent execution, artifact storage, and approval gates.
"""
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .repository import ContentOSRepository
from .artifact_store import ArtifactStore
from .state_machine import StateMachine
from .enums import WorkflowStage, RunStatus, StepStatus, ArtifactType, ApprovalType
from .agents.trend_radar_agent import TrendRadarAgent
from .agents.source_analyzer_agent import SourceAnalyzerAgent
from .agents.content_planner_agent import ContentPlannerAgent
from .agents.script_writer_agent import ScriptWriterAgent
from .agents.content_audit_agent import ContentAuditAgent
from .agents.script_reviser_agent import ScriptReviserAgent
from .exceptions import WorkflowError, InvalidTransitionError

logger = logging.getLogger(__name__)


@dataclass
class WorkflowConfig:
    """Configuration for workflow execution."""
    max_revision_attempts: int = 3
    auto_approve: bool = False
    auto_download_sources: bool = True


class ContentOSWorkflow:
    """
    Orchestrates the Content OS content creation workflow.
    
    Manages:
    - State transitions via StateMachine
    - Agent execution in correct order
    - Artifact storage via ArtifactStore
    - Database persistence via Repository
    - Approval gates
    - Error handling and retry logic
    """
    
    def __init__(
        self,
        repository: ContentOSRepository,
        artifact_store: ArtifactStore,
        config: Optional[WorkflowConfig] = None,
    ):
        self.repository = repository
        self.artifact_store = artifact_store
        self.config = config or WorkflowConfig()
        
        # Initialize agents
        self.agents = {
            "trend_radar": TrendRadarAgent(),
            "source_analyzer": SourceAnalyzerAgent(),
            "content_planner": ContentPlannerAgent(),
            "script_writer": ScriptWriterAgent(),
            "content_audit": ContentAuditAgent(),
            "script_reviser": ScriptReviserAgent(),
        }
        
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def start_run(self, run_id: int, user_id: int) -> Dict[str, Any]:
        """
        Start a workflow run.
        
        Args:
            run_id: Run ID from repository
            user_id: User ID for permission checks
            
        Returns:
            Updated run information
        """
        run = self.repository.get_run(run_id, user_id)
        if not run:
            raise WorkflowError(f"Run {run_id} not found")
        
        # Update status to running
        self.repository.update_run(
            run_id=run_id,
            user_id=user_id,
            status="running",
            started_at=time.time(),
        )
        
        self.logger.info(f"Starting workflow run {run_id}")
        
        # Execute workflow stages
        try:
            result = self._execute_workflow(run_id, user_id)
            
            # Mark as completed
            self.repository.update_run(
                run_id=run_id,
                user_id=user_id,
                status="completed",
                completed_at=time.time(),
                progress_percent=100,
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Workflow run {run_id} failed: {e}")
            import json
            self.repository.update_run(
                run_id=run_id,
                user_id=user_id,
                status="failed",
                error_json=json.dumps({"error": str(e), "type": type(e).__name__}),
            )
            raise
    
    def _execute_workflow(self, run_id: int, user_id: int) -> Dict[str, Any]:
        """
        Execute the workflow stages sequentially.
        
        Args:
            run_id: Run ID
            user_id: User ID
            
        Returns:
            Final workflow result
        """
        run = self.repository.get_run(run_id, user_id)
        project = self.repository.get_project(run.project_id, user_id)
        
        if not project:
            raise WorkflowError(f"Project {run.project_id} not found for run {run_id}")
        
        context = {
            "run_id": run_id,
            "user_id": user_id,
            "project_id": run.project_id,
            "topic": project.topic,
            "objective": project.objective,
            "target_platform": project.target_platform,
            "target_language": project.target_language,
            "target_duration_seconds": project.target_duration_seconds,
            "content_style": project.content_style,
            "visual_style": project.visual_style,
            "voice_id": project.voice_id,
            "subtitle_style_id": project.subtitle_style_id,
            "background_music_enabled": project.background_music_enabled,
            "settings": project.settings,
        }
        
        # Stage 1: Trend Research
        self._advance_stage(run_id, user_id, WorkflowStage.TREND_RESEARCH)
        trend_result = self._execute_agent(
            "trend_radar", run_id, user_id, context, WorkflowStage.TREND_RESEARCH
        )
        context["trend_result"] = trend_result
        
        # Stage 2: Source Selection (manual or from trends)
        self._advance_stage(run_id, user_id, WorkflowStage.SOURCE_SELECTION)
        sources = self._get_sources(run_id, user_id)
        context["sources"] = sources
        
        # Stage 3: Source Analysis
        self._advance_stage(run_id, user_id, WorkflowStage.SOURCE_ANALYSIS)
        analysis_result = self._execute_agent(
            "source_analyzer", run_id, user_id, context, WorkflowStage.SOURCE_ANALYSIS
        )
        context["source_analysis"] = analysis_result
        
        # Stage 4: Content Planning
        self._advance_stage(run_id, user_id, WorkflowStage.CONTENT_PLANNING)
        plan_result = self._execute_agent(
            "content_planner", run_id, user_id, context, WorkflowStage.CONTENT_PLANNING
        )
        context["content_plan"] = plan_result
        
        # Stage 5: Script Writing
        self._advance_stage(run_id, user_id, WorkflowStage.SCRIPT_WRITING)
        script_result = self._execute_agent(
            "script_writer", run_id, user_id, context, WorkflowStage.SCRIPT_WRITING
        )
        context["script"] = script_result
        
        # Stage 6: Script Audit (with revision loop)
        revision_count = 0
        while revision_count < self.config.max_revision_attempts:
            self._advance_stage(run_id, user_id, WorkflowStage.SCRIPT_AUDIT)
            audit_result = self._execute_agent(
                "content_audit", run_id, user_id, context, WorkflowStage.SCRIPT_AUDIT
            )
            context["audit_report"] = audit_result
            
            # Check if approval needed
            if audit_result["decision"] == "PASS":
                break
            elif audit_result["decision"] == "PASS_WITH_FIXES":
                # Need revision
                revision_count += 1
                self.repository.update_run(
                    run_id=run_id,
                    user_id=user_id,
                    revision_count=revision_count,
                )
                
                if revision_count >= self.config.max_revision_attempts:
                    # Max revisions reached, force approval
                    break
                
                # Execute revision
                self._advance_stage(run_id, user_id, WorkflowStage.SCRIPT_REVISION)
                revision_result = self._execute_agent(
                    "script_reviser", run_id, user_id, context, WorkflowStage.SCRIPT_REVISION
                )
                context["script"] = revision_result["revised_script"]
            else:
                # BLOCKED or FAIL - stop workflow
                self._advance_stage(run_id, user_id, WorkflowStage.BLOCKED)
                raise WorkflowError(f"Script audit failed: {audit_result['decision']}")
        
        # Stage 7: Await Approval
        self._advance_stage(run_id, user_id, WorkflowStage.AWAITING_APPROVAL)
        
        if self.config.auto_approve:
            # Auto-approve for testing - record approval first
            self._record_approval(run_id, user_id, ApprovalType.SCRIPT, "approved", "Auto-approved")
            # Now transition with has_approval=True
            StateMachine.validate_transition(
                from_stage=WorkflowStage.AWAITING_APPROVAL,
                to_stage=WorkflowStage.APPROVED,
                has_approval=True,
            )
            self.repository.update_run(
                run_id=run_id,
                user_id=user_id,
                current_stage="approved",
            )
        else:
            # Wait for manual approval
            self.logger.info(f"Run {run_id} awaiting manual approval")
            return {"status": "awaiting_approval", "run_id": run_id}
        
        # Stage 8: Ready for Localization
        self._advance_stage(run_id, user_id, WorkflowStage.READY_FOR_LOCALIZATION)
        
        return {
            "status": "ready_for_localization",
            "run_id": run_id,
            "script": context["script"],
            "content_plan": context["content_plan"],
        }
    
    def _advance_stage(self, run_id: int, user_id: int, new_stage: WorkflowStage) -> None:
        """Advance workflow stage with state machine validation."""
        run = self.repository.get_run(run_id, user_id)
        
        # Validate transition
        StateMachine.validate_transition(
            from_stage=WorkflowStage(run.current_stage),
            to_stage=new_stage,
        )
        
        # Update run
        self.repository.update_run(
            run_id=run_id,
            user_id=user_id,
            current_stage=new_stage.value,
            progress_percent=self._calculate_progress(new_stage),
        )
        
        self.logger.info(f"Run {run_id} advanced to {new_stage}")
    
    def _calculate_progress(self, stage: WorkflowStage) -> int:
        """Calculate progress percentage based on stage."""
        stage_order = [
            WorkflowStage.CREATED,
            WorkflowStage.TREND_RESEARCH,
            WorkflowStage.SOURCE_SELECTION,
            WorkflowStage.SOURCE_ANALYSIS,
            WorkflowStage.CONTENT_PLANNING,
            WorkflowStage.SCRIPT_WRITING,
            WorkflowStage.SCRIPT_AUDIT,
            WorkflowStage.SCRIPT_REVISION,
            WorkflowStage.AWAITING_APPROVAL,
            WorkflowStage.APPROVED,
            WorkflowStage.READY_FOR_LOCALIZATION,
            WorkflowStage.LOCALIZATION_RUNNING,
            WorkflowStage.RENDERED,
            WorkflowStage.COMPLETED,
        ]
        
        try:
            index = stage_order.index(stage)
            return int((index / len(stage_order)) * 100)
        except ValueError:
            return 0
    
    def _execute_agent(
        self,
        agent_name: str,
        run_id: int,
        user_id: int,
        context: Dict[str, Any],
        stage: WorkflowStage,
    ) -> Dict[str, Any]:
        """
        Execute an agent and store the result as an artifact.
        
        Args:
            agent_name: Name of agent to execute
            run_id: Run ID
            user_id: User ID
            context: Execution context
            stage: Current workflow stage
            
        Returns:
            Agent output
        """
        agent = self.agents[agent_name]
        run = self.repository.get_run(run_id, user_id)
        
        # Create step record
        step = self.repository.create_step(
            run_id=run_id,
            stage=stage.value,
            agent_name=agent_name,
        )
        
        self.logger.info(f"Executing {agent_name} for run {run_id}")
        
        try:
            # Execute agent
            output = agent.execute(context)
            
            # Store as artifact
            artifact_type = self._get_artifact_type_for_stage(stage)
            artifact = self.repository.create_artifact(
                run_id=run_id,
                user_id=user_id,
                artifact_type=artifact_type.value,
                version=1,
                schema_version="1.0",
                path=f"/artifacts/{run_id}/{artifact_type.value}.v1.json",
                checksum="mock_checksum",
                metadata={"stage": stage.value, "agent": agent_name},
                created_by_agent=agent_name,
            )
            
            # Also store in artifact store
            self.artifact_store.write(
                user_id=user_id,
                project_id=run.project_id,
                run_id=run_id,
                artifact_type=artifact_type,
                data=output,
                created_by_agent=agent_name,
            )
            
            # Update step
            self.repository.update_step(
                step_id=step.id,
                status="completed",
                output_artifact_ids_json=str([artifact.id]),
                completed_at=time.time(),
            )
            
            return output
            
        except Exception as e:
            self.logger.error(f"Agent {agent_name} failed: {e}")
            import json
            self.repository.update_step(
                step_id=step.id,
                status="failed",
                error_json=json.dumps({"error": str(e)}),
                completed_at=time.time(),
            )
            raise
    
    def _get_artifact_type_for_stage(self, stage: WorkflowStage) -> ArtifactType:
        """Map workflow stage to artifact type."""
        mapping = {
            WorkflowStage.TREND_RESEARCH: ArtifactType.TREND_REPORT,
            WorkflowStage.SOURCE_ANALYSIS: ArtifactType.SOURCE_ANALYSIS,
            WorkflowStage.CONTENT_PLANNING: ArtifactType.CONTENT_PLAN,
            WorkflowStage.SCRIPT_WRITING: ArtifactType.SCRIPT,
            WorkflowStage.SCRIPT_AUDIT: ArtifactType.AUDIT_REPORT,
            WorkflowStage.SCRIPT_REVISION: ArtifactType.REVISION_REPORT,
            WorkflowStage.SOURCE_SELECTION: ArtifactType.SELECTED_SOURCES,
        }
        return mapping.get(stage, ArtifactType.RUN_TRACE)
    
    def _get_sources(self, run_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Get sources for the run."""
        sources = self.repository.list_sources(run_id, user_id)
        return [s.__dict__ for s in sources]
    
    def _record_approval(
        self,
        run_id: int,
        user_id: int,
        approval_type: ApprovalType,
        decision: str,
        note: str = "",
    ) -> None:
        """Record an approval decision."""
        self.repository.create_approval(
            run_id=run_id,
            user_id=user_id,
            approval_type=approval_type.value,
            decision=decision,
            note=note,
        )
    
    def resume_run(self, run_id: int, user_id: int) -> Dict[str, Any]:
        """
        Resume a paused or awaiting-approval run.
        
        Args:
            run_id: Run ID
            user_id: User ID
            
        Returns:
            Updated run information
        """
        run = self.repository.get_run(run_id, user_id)
        if not run:
            raise WorkflowError(f"Run {run_id} not found")
        
        if run.status not in ["paused", "awaiting_approval"]:
            raise WorkflowError(f"Run {run_id} is not in a resumable state")
        
        self.logger.info(f"Resuming workflow run {run_id}")
        
        # Continue from current stage
        return self._execute_workflow(run_id, user_id)
    
    def cancel_run(self, run_id: int, user_id: int) -> None:
        """
        Cancel a running workflow run.
        
        Args:
            run_id: Run ID
            user_id: User ID
        """
        run = self.repository.get_run(run_id, user_id)
        if not run:
            raise WorkflowError(f"Run {run_id} not found")
        
        # Validate transition to cancelled
        StateMachine.validate_transition(
            from_stage=WorkflowStage(run.current_stage),
            to_stage=WorkflowStage.CANCELLED,
        )
        
        self.repository.update_run(
            run_id=run_id,
            user_id=user_id,
            status="cancelled",
            current_stage="cancelled",
        )
        
        self.logger.info(f"Run {run_id} cancelled")
    
    def submit_approval(
        self,
        run_id: int,
        user_id: int,
        approval_type: ApprovalType,
        decision: str,
        note: str = "",
    ) -> Dict[str, Any]:
        """
        Submit an approval decision for a run.
        
        Args:
            run_id: Run ID
            user_id: User ID
            approval_type: Type of approval
            decision: Decision (approved/rejected)
            note: Optional note
            
        Returns:
            Updated run information
        """
        run = self.repository.get_run(run_id, user_id)
        if not run:
            raise WorkflowError(f"Run {run_id} not found")
        
        # Record approval
        self._record_approval(run_id, user_id, approval_type, decision, note)
        
        # Advance based on decision
        if decision == "approved":
            StateMachine.validate_transition(
                from_stage=WorkflowStage(run.current_stage),
                to_stage=WorkflowStage.APPROVED,
                has_approval=True,
            )
            self.repository.update_run(
                run_id=run_id,
                user_id=user_id,
                current_stage="approved",
            )
            
            # Continue workflow from READY_FOR_LOCALIZATION
            self._advance_stage(run_id, user_id, WorkflowStage.READY_FOR_LOCALIZATION)
            
            return {
                "status": "ready_for_localization",
                "run_id": run_id,
            }
        else:
            # Rejected - cancel (requires approval record since we just created one)
            StateMachine.validate_transition(
                from_stage=WorkflowStage(run.current_stage),
                to_stage=WorkflowStage.CANCELLED,
                has_approval=True,
            )
            self.repository.update_run(
                run_id=run_id,
                user_id=user_id,
                status="cancelled",
                current_stage="cancelled",
            )
            return {"status": "cancelled", "run_id": run_id}
