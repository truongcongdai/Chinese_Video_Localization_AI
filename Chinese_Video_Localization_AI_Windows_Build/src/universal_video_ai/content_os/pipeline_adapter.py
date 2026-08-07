"""
Adapter to convert Content OS runs into existing localization jobs.

Bridges the Content OS workflow with the existing video localization pipeline
by creating jobs in the web store that the orchestrator can process.
"""
import logging
import time
import uuid
from typing import Dict, Any, Optional, List

from .repository import ContentOSRepository
from .artifact_store import ArtifactStore
from .enums import WorkflowStage
from .exceptions import WorkflowError

logger = logging.getLogger(__name__)


class PipelineAdapter:
    """
    Adapter to convert Content OS runs to localization jobs.
    
    Takes an approved Content OS run with generated script and creates
    a job in the web store that the existing localization pipeline can process.
    """
    
    def __init__(
        self,
        repository: ContentOSRepository,
        artifact_store: ArtifactStore,
        web_store_db_path,
    ):
        self.repository = repository
        self.artifact_store = artifact_store
        self.web_store_db_path = web_store_db_path
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def create_job_from_run(
        self,
        run_id: int,
        user_id: int,
        source_url: Optional[str] = None,
    ) -> str:
        """
        Create a localization job from an approved Content OS run.
        
        Args:
            run_id: Content OS run ID
            user_id: User ID
            source_url: Optional source video URL (if using existing video)
            
        Returns:
            Job ID from the web store
        """
        # Get the run and project
        run = self.repository.get_run(run_id, user_id)
        if not run:
            raise WorkflowError(f"Run {run_id} not found")
        
        project = self.repository.get_project(run.project_id, user_id)
        if not project:
            raise WorkflowError(f"Project {run.project_id} not found")
        
        production_ready_stages = {
            "approved",
            "ready_for_localization",
            "storyboarding",
            "awaiting_storyboard_approval",
            "asset_planning",
            "asset_resolving",
            "assets_ready",
            "voice_generation",
            "subtitle_generation",
            "timeline_building",
            "rendering",
            "output_validation",
            "completed",
            "failed",
        }
        if run.current_stage not in production_ready_stages:
            raise WorkflowError(
                f"Run {run_id} is not ready for localization. "
                f"Current stage: {run.current_stage}"
            )
        
        # Get the script artifact
        script_artifact = self._get_latest_artifact(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type="script",
        )
        
        if not script_artifact:
            raise WorkflowError(f"No script artifact found for run {run_id}")
        
        # script_artifact is already the data dict (extracted by _get_latest_artifact)
        script_data = script_artifact
        
        # Get the content plan for context
        plan_artifact = self._get_latest_artifact(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type="content_plan",
        )
        
        # plan_artifact is already the data dict (extracted by _get_latest_artifact)
        content_plan = plan_artifact if plan_artifact else {}
        
        # Get selected sources
        sources = self.repository.list_sources(run_id, user_id)
        selected_sources = [s for s in sources if s.selected == 1]
        
        # Use the first selected source URL if no source_url provided
        if not source_url and selected_sources:
            source_url = selected_sources[0].source_url
        
        # Create job in web store (Store.create_job will generate the job_id)
        actual_job_id = self._create_web_store_job(
            run_id=run_id,
            user_id=user_id,
            project=project,
            script=script_data,
            content_plan=content_plan,
            source_url=source_url,
            selected_sources=selected_sources,
        )
        
        # Update run to track the created job
        import json
        self.repository.update_run(
            run_id=run_id,
            user_id=user_id,
            warning_json=json.dumps({"localization_job_id": actual_job_id}),
        )
        
        self.logger.info(f"Created localization job {actual_job_id} from Content OS run {run_id}")
        
        return actual_job_id
    
    def _get_latest_artifact(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
        artifact_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the latest artifact of a given type."""
        try:
            from .enums import ArtifactType
            # Convert string to enum
            artifact_type_enum = ArtifactType(artifact_type)
            artifact = self.artifact_store.read(
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                artifact_type=artifact_type_enum,
            )
            # Extract the data field from the artifact wrapper
            if artifact and isinstance(artifact, dict):
                return artifact.get("data", artifact)
            return artifact
        except Exception as e:
            self.logger.warning(f"Failed to read artifact {artifact_type}: {e}")
            return None
    
    def _create_web_store_job(
        self,
        run_id: int,
        user_id: int,
        project,
        script: Dict[str, Any],
        content_plan: Dict[str, Any],
        source_url: Optional[str],
        selected_sources: List,
    ) -> str:
        """
        Create a job record in the web store.
        
        Args:
            job_id: Job ID
            user_id: User ID
            project: Content OS project
            script: Generated script data
            content_plan: Content plan data
            source_url: Source video URL
            selected_sources: Selected source videos
        """
        import json
        
        # Extract script details
        title = script.get("title_options", ["Content OS Video"])[0] if script.get("title_options") else "Content OS Video"
        segments = script.get("segments", [])
        
        # Build segments_json for the job
        segments_data = []
        for i, seg in enumerate(segments):
            segment_text = seg.get("text") or seg.get("subtitle_text") or seg.get("narration") or ""
            segments_data.append({
                "index": i,
                "start": seg.get("start_second", 0),
                "end": seg.get("end_second", 0),
                "text": segment_text,
                "narration": seg.get("narration", segment_text),
                "visual_instruction": seg.get("visual_instruction", ""),
            })
        
        # Use source URL from selected sources if available
        if not source_url and selected_sources:
            source_url = selected_sources[0].source_url
        
        # If still no source URL, use a placeholder (will need manual source)
        if not source_url:
            source_url = "content_os://generated_script"
        
        # Use Store.create_job for proper job creation
        from ..web.store import Store
        store = Store(str(self.web_store_db_path))
        
        try:
            created_job = store.create_job(
                user_id=user_id,
                source_url=source_url,
                target_language=project.target_language,
                source_language=f"content_os:{run_id}",
                tts_provider="edge",
                tts_voice="",
            )
            self.logger.info(f"Store.create_job returned job {created_job.id}")
        except Exception as e:
            self.logger.error(f"Store.create_job failed: {e}")
            raise
        
        # Update job title and segments after creation
        try:
            store.set_job_segments(created_job.id, segments_data)
            self.logger.info(f"Set segments for job {created_job.id}")
        except Exception as e:
            self.logger.error(f"set_job_segments failed: {e}")
        
        # Update title directly in database
        import sqlite3
        import time
        try:
            with sqlite3.connect(str(self.web_store_db_path)) as conn:
                conn.execute(
                    "UPDATE jobs SET title = ?, updated_at = ? WHERE id = ?",
                    (title, time.time(), created_job.id)
                )
                conn.commit()
                self.logger.info(f"Updated title for job {created_job.id}")
        except Exception as e:
            self.logger.error(f"Update title failed: {e}")
        
        # Verify job was inserted
        try:
            with sqlite3.connect(str(self.web_store_db_path)) as conn:
                cursor = conn.execute("SELECT id, title FROM jobs WHERE id = ?", (created_job.id,))
                row = cursor.fetchone()
                if row:
                    self.logger.info(f"Verified job {created_job.id} exists in database with title: {row[1]}")
                else:
                    self.logger.error(f"Job {created_job.id} NOT FOUND in database after creation!")
        except Exception as e:
            self.logger.error(f"Verification failed: {e}")
        
        # NOTE: Do NOT mark job as done immediately. Content OS jobs need to go through
        # the production pipeline (storyboard → assets → voice → subtitles → timeline → render)
        # to generate an actual video file. The job will be marked as done after rendering completes.
        
        # Update the job_id to match the one created by Store
        actual_job_id = created_job.id
        
        self.logger.info(f"Inserted job {actual_job_id} into web store")
        return actual_job_id
    
    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of a localization job.
        
        Args:
            job_id: Job ID
            
        Returns:
            Job status dictionary or None if not found
        """
        import sqlite3
        
        with sqlite3.connect(str(self.web_store_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id, status, progress_note, error, source_url, source_language, created_at, updated_at FROM jobs WHERE id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    
    def update_run_from_job_status(
        self,
        run_id: int,
        user_id: int,
        job_id: str,
    ) -> None:
        """
        Update Content OS run based on job status.
        
        Args:
            run_id: Content OS run ID
            user_id: User ID
            job_id: Localization job ID
        """
        job_status = self.get_job_status(job_id)
        if not job_status:
            self.logger.warning(f"Job {job_id} not found")
            return
        
        status = job_status["status"]
        
        # Map job status to workflow stage
        stage_mapping = {
            "queued": WorkflowStage.READY_FOR_LOCALIZATION,
            "running": WorkflowStage.LOCALIZATION_RUNNING,
            "review": WorkflowStage.RENDERED,
            "done": WorkflowStage.COMPLETED,
            "error": WorkflowStage.FAILED,
        }
        
        target_stage = stage_mapping.get(status)
        if target_stage:
            try:
                from .state_machine import StateMachine
                run = self.repository.get_run(run_id, user_id)
                current_stage = WorkflowStage(run.current_stage)
                
                StateMachine.validate_transition(current_stage, target_stage)
                
                self.repository.update_run(
                    run_id=run_id,
                    user_id=user_id,
                    current_stage=target_stage.value,
                    status=status,
                )
                
                if status == "done":
                    self.repository.update_run(
                        run_id=run_id,
                        user_id=user_id,
                        completed_at=time.time(),
                        progress_percent=100,
                    )
                
                self.logger.info(f"Updated run {run_id} to {target_stage} based on job {job_id} status")
                
            except Exception as e:
                self.logger.warning(f"Failed to update run stage: {e}")
