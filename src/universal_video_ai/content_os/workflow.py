"""
Content OS workflow orchestration layer.

Coordinates the entire content creation workflow, managing state transitions,
agent execution, artifact storage, and approval gates.
"""
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .repository import ContentOSRepository
from .artifact_store import ArtifactStore
from .state_machine import StateMachine
from .enums import WorkflowStage, RunStatus, StepStatus, ArtifactType, ApprovalType
from .asset_resolver import AssetType as AssetResolverAssetType, AssetSource
from .agents.trend_radar_agent import TrendRadarAgent
from .agents.source_analyzer_agent import SourceAnalyzerAgent
from .agents.content_planner_agent import ContentPlannerAgent
from .agents.script_writer_agent import ScriptWriterAgent
from .agents.content_audit_agent import ContentAuditAgent
from .agents.script_reviser_agent import ScriptReviserAgent
from .storyboard import StoryboardManager
from .asset_resolver import AssetResolver
from .renderer import Renderer, MP4Validator
from .adapters import TTSAdapter, SubtitleAdapter, TimelineAdapter
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

        # Initialize production components
        self.storyboard_manager = StoryboardManager(repository)
        self.asset_resolver = AssetResolver(repository)
        self.renderer = Renderer(repository)
        self.mp4_validator = MP4Validator()

        # Initialize adapters
        self.tts_adapter = TTSAdapter()
        self.subtitle_adapter = SubtitleAdapter()
        self.timeline_adapter = TimelineAdapter()

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

        if run.status == "running":
            raise WorkflowError(f"Run {run_id} is already running")
        if run.status == "completed":
            raise WorkflowError(f"Run {run_id} is already completed")
        if run.status in {"paused", "awaiting_approval"}:
            raise WorkflowError(
                f"Run {run_id} is {run.status}; use the resume or approval action instead"
            )

        # _execute_workflow currently performs a full run from TREND_RESEARCH.
        # A failed attempt retains its last stage for diagnostics, so retrying it
        # without resetting would try to transition backwards (for example,
        # SOURCE_ANALYSIS -> TREND_RESEARCH) and fail before doing useful work.
        # Reset only retryable terminal attempts; new runs are already CREATED.
        if run.status in {"failed", "cancelled"} or run.current_stage != WorkflowStage.CREATED.value:
            self.repository.update_run(
                run_id=run_id,
                user_id=user_id,
                status="created",
                current_stage=WorkflowStage.CREATED.value,
                progress_percent=0,
                revision_count=0,
                warning_json=None,
                error_json=None,
                started_at=None,
                completed_at=None,
            )

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
            "user_instructions": f"{project.topic} {project.objective}",  # Combine topic and objective for context
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

        # Continue with production stages if auto_approve is enabled
        if self.config.auto_approve:
            return self._execute_production_stages(run_id, user_id, context)

        return {
            "status": "ready_for_localization",
            "run_id": run_id,
            "script": context["script"],
            "content_plan": context["content_plan"],
        }

    def _execute_production_stages(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute production stages: storyboard → assets → voice → subtitles → timeline → render → validate.

        Args:
            run_id: Run ID
            user_id: User ID
            context: Execution context with script, content_plan, etc.

        Returns:
            Final workflow result with MP4 output path
        """
        run = self.repository.get_run(run_id, user_id)
        project = self.repository.get_project(run.project_id, user_id)

        # Stage 9: Storyboarding
        self._advance_stage(run_id, user_id, WorkflowStage.STORYBOARDING)
        storyboard = self._generate_storyboard(run_id, user_id, context)
        context["storyboard"] = storyboard

        # Stage 10: Storyboard Approval (auto-approve in this mode)
        self._advance_stage(run_id, user_id, WorkflowStage.AWAITING_STORYBOARD_APPROVAL)
        self._record_approval(run_id, user_id, ApprovalType.STORYBOARD, "approved", "Auto-approved")
        self._advance_stage(run_id, user_id, WorkflowStage.ASSET_PLANNING, has_approval=True)

        # Stage 11: Asset Planning
        asset_manifest = self._plan_assets(run_id, user_id, context)
        context["asset_manifest"] = asset_manifest

        # Stage 12: Asset Resolution
        self._advance_stage(run_id, user_id, WorkflowStage.ASSET_RESOLVING)
        resolved_assets = self._resolve_assets(run_id, user_id, context)
        context["resolved_assets"] = resolved_assets

        # Stage 13: Assets Ready
        self._advance_stage(run_id, user_id, WorkflowStage.ASSETS_READY)

        # Stage 14: Voice Generation (TTS)
        self._advance_stage(run_id, user_id, WorkflowStage.VOICE_GENERATION)
        voice_manifest = self._generate_voice(run_id, user_id, context)
        context["voice_manifest"] = voice_manifest

        # Stage 15: Subtitle Generation
        self._advance_stage(run_id, user_id, WorkflowStage.SUBTITLE_GENERATION)
        subtitle_manifest = self._generate_subtitles(run_id, user_id, context)
        context["subtitle_manifest"] = subtitle_manifest

        # Stage 16: Timeline Building
        self._advance_stage(run_id, user_id, WorkflowStage.TIMELINE_BUILDING)
        timeline = self._build_timeline(run_id, user_id, context)
        context["timeline"] = timeline

        # Stage 17: Rendering
        self._advance_stage(run_id, user_id, WorkflowStage.RENDERING)
        render_result = self._render_video(run_id, user_id, context)
        context["render_result"] = render_result

        # Stage 18: Output Validation
        self._advance_stage(run_id, user_id, WorkflowStage.OUTPUT_VALIDATION)
        validation_result = self._validate_output(run_id, user_id, context)
        context["validation_result"] = validation_result

        # Stage 19: Completed
        self._advance_stage(run_id, user_id, WorkflowStage.COMPLETED)

        return {
            "status": "completed",
            "run_id": run_id,
            "output_path": render_result.get("output_path"),
            "validation": validation_result,
        }

    def _generate_storyboard(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate storyboard from approved script."""
        run = self.repository.get_run(run_id, user_id)
        script = context["script"]

        storyboard = self.storyboard_manager.create_from_script(
            run_id=run_id,
            user_id=user_id,
            script=script,
        )

        # Convert to JSON-serializable format
        storyboard_data = {
            "run_id": storyboard.run_id,
            "user_id": storyboard.user_id,
            "scenes": [
                {
                    "scene_id": scene.scene_id,
                    "order": scene.order,
                    "start_second": scene.start_second,
                    "end_second": scene.end_second,
                    "visual_instruction": scene.visual_instruction,
                    "subtitle_text": scene.subtitle_text,
                    "narration_text": scene.narration_text,
                    "camera_angle": scene.camera_angle,
                    "transition": scene.transition,
                    "notes": scene.notes,
                    "assets": scene.assets,
                }
                for scene in storyboard.scenes
            ],
            "status": storyboard.status.value if hasattr(storyboard.status, 'value') else str(storyboard.status),
            "version": storyboard.version,
            "created_at": storyboard.created_at,
            "updated_at": storyboard.updated_at,
        }

        # Store as artifact
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.STORYBOARD,
            data=storyboard_data,
            created_by_agent="StoryboardManager",
        )

        return storyboard_data

    def _plan_assets(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Plan visual assets based on storyboard."""
        run = self.repository.get_run(run_id, user_id)
        storyboard = context["storyboard"]

        # Extract asset requirements from storyboard scenes
        asset_requirements = []
        for scene in storyboard.get("scenes", []):
            asset_requirements.append({
                "scene_id": scene.get("scene_id"),
                "visual_strategy": scene.get("visual_strategy", "generated_image"),
                "visual_prompt": scene.get("visual_instruction", ""),
            })

        manifest = {
            "requirements": asset_requirements,
            "total_scenes": len(asset_requirements),
        }

        # Store as artifact
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.ASSET_MANIFEST,
            data=manifest,
            created_by_agent="Workflow",
        )

        return manifest

    def _resolve_assets(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve visual assets using asset resolver."""
        run = self.repository.get_run(run_id, user_id)
        storyboard = context["storyboard"]
        project = self.repository.get_project(run.project_id, user_id)
        script = context.get("script", {})

        resolved_assets = []
        scenes = storyboard.get("scenes", [])
        visual_mode = "none"
        add_generated_intro = (os.getenv("CONTENT_OS_ADD_GENERATED_INTRO") or "false").lower() == "true"
        if scenes and add_generated_intro:
            script_title = ""
            titles = script.get("title_options") or []
            if titles:
                script_title = titles[0]
            intro_prompt = (
                "Vertical 9:16 realistic opening shot for a Vietnamese mobile learning video. "
                f"Topic/title: {script_title or project.topic}. "
                "A modern smartphone on a study desk, soft cinematic light, AI learning symbols subtly around it, "
                "clear visual hook, no logos, no readable text, leave lower 28 percent clean for subtitles."
            )
            intro_asset = self.asset_resolver.resolve_asset(
                run_id=run_id,
                user_id=user_id,
                asset_type=AssetResolverAssetType.IMAGE,
                description=intro_prompt,
                preferred_sources=[AssetSource.STOCK_API, AssetSource.GENERATED],
            )
            first_scene_duration = max(
                1.0,
                float(scenes[0].get("end_second", 2.0) or 2.0) - float(scenes[0].get("start_second", 0.0) or 0.0),
            )
            intro_duration = min(2.0, first_scene_duration * 0.4)
            if intro_asset and intro_asset.local_path:
                resolved_assets.append({
                    "scene_id": "scene_intro",
                    "strategy": "generated_intro",
                    "local_path": intro_asset.local_path,
                    "description": intro_prompt,
                    "start_second": 0.0,
                    "end_second": intro_duration,
                    "duration_seconds": intro_duration,
                    "generator": intro_asset.metadata.get("generator") if intro_asset.metadata else None,
                })

        seen_asset_checksums = set()
        for scene_index, scene in enumerate(scenes):
            scene_id = scene.get("scene_id")
            visual_instruction = scene.get("visual_instruction", "")
            narration_text = scene.get("narration_text", "")

            # Use narration text as description if visual instruction is empty
            description = visual_instruction if visual_instruction else narration_text

            # Generate one independent asset per scene. Hybrid mode tries a real
            # Gemini video first, then falls back to a unique image that the
            # renderer animates. This prevents one fallback image from being
            # reused across the whole timeline.
            settings = project.settings if hasattr(project, "settings") else {}
            visual_mode = str(
                settings.get("visual_generation_mode")
                or os.getenv("CONTENT_OS_VISUAL_GENERATION_MODE")
                or "hybrid"
            ).strip().lower()
            asset = None
            if visual_mode in {"video", "hybrid", "gemini_video"}:
                asset = self.asset_resolver.resolve_asset(
                    run_id=run_id, user_id=user_id,
                    asset_type=AssetResolverAssetType.VIDEO,
                    description=description,
                    preferred_sources=[AssetSource.GENERATED],
                )
                if asset and not asset.local_path:
                    asset = None
            if asset is None:
                asset = self.asset_resolver.resolve_asset(
                    run_id=run_id, user_id=user_id,
                    asset_type=AssetResolverAssetType.IMAGE,
                    description=description,
                    preferred_sources=[AssetSource.GENERATED, AssetSource.STOCK_API],
                )

            if asset and asset.local_path:
                # Reject byte-identical outputs across scenes. Gemini or a local
                # fallback can occasionally return the same frame repeatedly.
                # Retry once with scene-specific composition constraints.
                asset_path_obj = Path(asset.local_path)
                asset_checksum = hashlib.sha256(asset_path_obj.read_bytes()).hexdigest() if asset_path_obj.exists() else ""
                if asset_checksum and asset_checksum in seen_asset_checksums:
                    retry_description = (
                        f"{description} Scene {scene_index + 1} must use a visibly different composition, human pose, "
                        "camera angle, setting, foreground object, and action from every previous scene."
                    )
                    retry_asset = self.asset_resolver.resolve_asset(
                        run_id=run_id, user_id=user_id,
                        asset_type=AssetResolverAssetType.IMAGE,
                        description=retry_description,
                        preferred_sources=[AssetSource.GENERATED, AssetSource.STOCK_API],
                    )
                    if retry_asset and retry_asset.local_path:
                        retry_path = Path(retry_asset.local_path)
                        retry_checksum = hashlib.sha256(retry_path.read_bytes()).hexdigest() if retry_path.exists() else ""
                        if retry_checksum and retry_checksum not in seen_asset_checksums:
                            asset, asset_checksum = retry_asset, retry_checksum
                if asset_checksum:
                    seen_asset_checksums.add(asset_checksum)
                start_second = float(scene.get("start_second", 0.0) or 0.0)
                end_second = float(scene.get("end_second", start_second + 5.0) or start_second + 5.0)
                if end_second <= start_second:
                    end_second = start_second + 5.0
                if scene_index == 0 and resolved_assets and resolved_assets[0].get("scene_id") == "scene_intro":
                    intro_duration = float(resolved_assets[0].get("duration_seconds") or 0.0)
                    start_second += intro_duration
                    if end_second <= start_second:
                        end_second = start_second + 0.5
                resolved_assets.append({
                    "scene_id": scene_id,
                    "strategy": "generated",
                    "local_path": asset.local_path,
                    "description": description,
                    "start_second": start_second,
                    "end_second": end_second,
                    "duration_seconds": end_second - start_second,
                    "generator": asset.metadata.get("generator") if asset.metadata else None,
                    "asset_type": asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type),
                    "motion": scene.get("camera_motion") or scene.get("motion") or ("video_native" if str(asset.asset_type).endswith("video") else ["slow_zoom_in", "slow_pan_left", "slow_pan_right", "gentle_push_in"][scene_index % 4]),
                    "transition": scene.get("transition_out") or "fade",
                })
            else:
                # Fallback to text card info
                resolved_assets.append({
                    "scene_id": scene_id,
                    "strategy": "fallback_text_card",
                    "local_path": None,
                    "description": description,
                })

        # Asset quality gate: all planned scenes must have a real local asset,
        # and repeated bytes are reported instead of silently accepted.
        unique_hashes = set()
        duplicate_scene_ids = []
        for item in resolved_assets:
            path = item.get("local_path")
            path_obj = Path(path) if path else None
            if path_obj is None or not path_obj.exists() or not path_obj.is_file():
                continue
            try:
                digest = hashlib.sha256(path_obj.read_bytes()).hexdigest()
            except OSError as exc:
                logger.warning("Unable to checksum resolved asset %s: %s", path_obj, exc)
                continue
            item["checksum"] = digest
            if digest in unique_hashes:
                duplicate_scene_ids.append(item.get("scene_id"))
            unique_hashes.add(digest)

        manifest = {
            "assets": resolved_assets,
            "total_assets": len(resolved_assets),
            "unique_assets": len(unique_hashes),
            "duplicate_scene_ids": duplicate_scene_ids,
            "visual_generation_mode": visual_mode if scenes else "none",
        }

        # Store as artifact
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.RESOLVED_ASSETS,
            data=manifest,
            created_by_agent="AssetResolver",
        )

        return manifest

    def _generate_voice(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate TTS narration using TTS adapter."""
        run = self.repository.get_run(run_id, user_id)
        script = context["script"]
        project = self.repository.get_project(run.project_id, user_id)

        # Extract narration segments from script
        segments = script.get("segments", [])
        narration_text = " ".join([seg.get("narration", seg.get("text", "")) for seg in segments])

        # Content OS scripts are generated for the project's target_language.
        # Do not infer English from ASCII ratio: Vietnamese text is mostly ASCII too
        # and that caused Vietnamese scripts to be spoken by en-US voices.
        script_language = (project.target_language or "vi").strip() or "vi"
        voice_id = (project.voice_id or "").strip() or None

        # Generate audio using TTS adapter with detected script language
        try:
            audio_path = self.tts_adapter.generate_audio(
                text=narration_text,
                language=script_language,
                voice_id=voice_id,
                output_dir=self.artifact_store._get_run_dir(user_id, run.project_id, run_id),
            )

            # Get actual duration
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, text=True
            )
            duration = float(result.stdout.strip()) if result.stdout.strip() else 0.0

            manifest = {
                "audio_path": str(audio_path),
                "duration_seconds": duration,
                "language": script_language,
                "voice_id": voice_id or "",
                "segments_count": len(segments),
                "text_source": "segments.narration",
            }
        except Exception as e:
            self.logger.warning(f"TTS generation failed: {e}, using fallback")
            # Fallback: create manifest without real audio
            manifest = {
                "audio_path": None,
                "duration_seconds": project.target_duration_seconds,
                "language": project.target_language,
                "voice_id": project.voice_id,
                "segments_count": len(segments),
                "error": str(e),
            }

        # Store as artifact
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.VOICE_MANIFEST,
            data=manifest,
            created_by_agent="TTSAdapter",
        )

        return manifest

    def _generate_subtitles(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate subtitles using subtitle adapter."""
        run = self.repository.get_run(run_id, user_id)
        script = context["script"]
        voice_manifest = context["voice_manifest"]
        project = self.repository.get_project(run.project_id, user_id)

        # Generate subtitles from script segments
        segments = script.get("segments", [])
        duration = voice_manifest.get("duration_seconds", project.target_duration_seconds)

        try:
            subtitle_path = self.subtitle_adapter.generate_subtitles(
                segments=segments,
                duration=duration,
                output_dir=self.artifact_store._get_run_dir(user_id, run.project_id, run_id),
            )

            manifest = {
                "subtitle_path": str(subtitle_path),
                "format": subtitle_path.suffix.lstrip(".") or "ass",
                "segments_count": len(segments),
                "duration_seconds": duration,
            }
        except Exception as e:
            self.logger.warning(f"Subtitle generation failed: {e}, using fallback")
            manifest = {
                "subtitle_path": None,
                "format": "srt",
                "segments_count": len(segments),
                "duration_seconds": duration,
                "error": str(e),
            }

        # Store as artifact
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.SUBTITLE_MANIFEST,
            data=manifest,
            created_by_agent="SubtitleAdapter",
        )

        return manifest

    def _build_timeline(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Build timeline using timeline adapter."""
        run = self.repository.get_run(run_id, user_id)
        script = context["script"]
        voice_manifest = context["voice_manifest"]
        subtitle_manifest = context["subtitle_manifest"]
        resolved_assets = context["resolved_assets"]
        project = self.repository.get_project(run.project_id, user_id)

        try:
            timeline = self.timeline_adapter.build_timeline(
                script=script,
                voice_manifest=voice_manifest,
                subtitle_manifest=subtitle_manifest,
                assets=resolved_assets,
                target_platform=project.target_platform,
                target_duration=project.target_duration_seconds,
            )
        except Exception as e:
            self.logger.warning(f"Timeline building failed: {e}, using fallback")
            # Fallback timeline
            timeline = {
                "duration_seconds": project.target_duration_seconds,
                "resolution": self._get_resolution_for_platform(project.target_platform),
                "video_tracks": [],
                "audio_tracks": [],
                "subtitle_tracks": [],
                "error": str(e),
            }

        # Store as artifact
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.TIMELINE,
            data=timeline,
            created_by_agent="TimelineAdapter",
        )

        return timeline

    def _render_video(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Render video using renderer integration."""
        run = self.repository.get_run(run_id, user_id)
        timeline = context["timeline"]
        project = self.repository.get_project(run.project_id, user_id)

        # Prepare output path
        output_dir = self.artifact_store._get_run_dir(user_id, run.project_id, run_id) / "renders"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "final.mp4"

        # Extract audio and subtitle paths from context
        voice_manifest = context.get("voice_manifest", {})
        subtitle_manifest = context.get("subtitle_manifest", {})

        audio_path = voice_manifest.get("audio_path") if isinstance(voice_manifest, dict) else None
        subtitle_path = subtitle_manifest.get("subtitle_path") if isinstance(subtitle_manifest, dict) else None

        # Get duration from timeline or project
        duration = timeline.get("total_duration", project.target_duration_seconds) if isinstance(timeline, dict) else project.target_duration_seconds

        try:
            # Store render job with audio/subtitle metadata
            render_job = self.renderer.submit_render_job(
                run_id=run_id,
                user_id=user_id,
                timeline_path=str(output_dir / "timeline.json"),
                output_path=str(output_path),
            )

            # Update render job metadata with audio/subtitle/assets info
            render_job.metadata.update({
                "audio_path": audio_path,
                "subtitle_path": subtitle_path,
                "duration": duration,
                "resolution": self._get_resolution_for_platform(project.target_platform),
                "assets": context.get("resolved_assets", {}),
                "branding_config": (project.settings or {}).get("branding_config", {}),
                "branding_context": [user_id, run_id, project.channel_name, project.topic],
            })

            # Start render with actual audio and subtitles
            render_job = self.renderer.start_render(render_job)
            if render_job.status.value != "completed":
                raise RuntimeError(render_job.error_message or "Render failed")

            result = {
                "output_path": str(output_path),
                "job_id": render_job.job_id,
                "status": render_job.status.value,
                "resolution": self._get_resolution_for_platform(project.target_platform),
            }
        except Exception as e:
            self.logger.error(f"Rendering failed: {e}")
            result = {
                "output_path": str(output_path),
                "job_id": "failed_render_job",
                "status": "failed",
                "resolution": self._get_resolution_for_platform(project.target_platform),
                "error": str(e),
            }
            self.artifact_store.write(
                user_id=user_id,
                project_id=run.project_id,
                run_id=run_id,
                artifact_type=ArtifactType.RENDER_REPORT,
                data=result,
                created_by_agent="Renderer",
            )
            raise

        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.RENDER_REPORT,
            data=result,
            created_by_agent="Renderer",
        )

        return result

    def _validate_output(self, run_id: int, user_id: int, context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate MP4 output using FFprobe."""
        render_result = context["render_result"]
        project = self.repository.get_project(
            self.repository.get_run(run_id, user_id).project_id,
            user_id
        )

        output_path = render_result.get("output_path")
        timeline = context.get("timeline", {}) if isinstance(context, dict) else {}
        expected_duration = (
            float(timeline.get("total_duration") or timeline.get("duration_seconds"))
            if isinstance(timeline, dict) and (timeline.get("total_duration") or timeline.get("duration_seconds"))
            else project.target_duration_seconds
        )
        expected_resolution = self._get_resolution_for_platform(project.target_platform)

        if output_path and Path(output_path).exists():
            try:
                validation = self.mp4_validator.validate(
                    file_path=output_path,
                    expected_duration=expected_duration,
                    expected_resolution=expected_resolution,
                )

                result = {
                    "valid": validation.status.value == "valid",
                    "file_path": output_path,
                    "file_size_bytes": validation.file_size_bytes,
                    "duration_seconds": validation.duration_seconds,
                    "resolution": validation.resolution,
                    "video_codec": validation.video_codec,
                    "audio_codec": validation.audio_codec,
                    "issues": validation.issues,
                    "warnings": validation.warnings,
                }
            except Exception as e:
                self.logger.error(f"Validation failed: {e}")
                result = {
                    "valid": False,
                    "file_path": output_path,
                    "error": str(e),
                }
        else:
            result = {
                "valid": False,
                "file_path": output_path,
                "error": "File does not exist",
            }

        # Store as artifact
        run = self.repository.get_run(run_id, user_id)
        self.artifact_store.write(
            user_id=user_id,
            project_id=run.project_id,
            run_id=run_id,
            artifact_type=ArtifactType.OUTPUT_VALIDATION,
            data=result,
            created_by_agent="MP4Validator",
        )

        return result

    def _get_resolution_for_platform(self, platform: str) -> str:
        """Get resolution for target platform."""
        resolutions = {
            "youtube_shorts": "1080x1920",
            "facebook_reels": "1080x1920",
            "tiktok": "1080x1920",
            "youtube_landscape": "1920x1080",
            "instagram_reels": "1080x1920",
        }
        return resolutions.get(platform, "1080x1920")

    def _advance_stage(self, run_id: int, user_id: int, new_stage: WorkflowStage, has_approval: bool = False) -> None:
        """Advance workflow stage with state machine validation."""
        run = self.repository.get_run(run_id, user_id)

        # Check if current stage requires approval and if approval exists
        current_stage = WorkflowStage(run.current_stage)
        if not has_approval and current_stage in StateMachine._APPROVAL_REQUIRED:
            # Check if approval exists for this stage
            approvals = self.repository.list_approvals(run_id)
            has_approval = len(approvals) > 0

        # Validate transition
        StateMachine.validate_transition(
            from_stage=current_stage,
            to_stage=new_stage,
            has_approval=has_approval,
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
            WorkflowStage.STORYBOARDING,
            WorkflowStage.AWAITING_STORYBOARD_APPROVAL,
            WorkflowStage.ASSET_PLANNING,
            WorkflowStage.ASSET_RESOLVING,
            WorkflowStage.ASSETS_READY,
            WorkflowStage.VOICE_GENERATION,
            WorkflowStage.SUBTITLE_GENERATION,
            WorkflowStage.TIMELINE_BUILDING,
            WorkflowStage.RENDERING,
            WorkflowStage.OUTPUT_VALIDATION,
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
            WorkflowStage.STORYBOARDING: ArtifactType.STORYBOARD,
            WorkflowStage.ASSET_PLANNING: ArtifactType.ASSET_MANIFEST,
            WorkflowStage.ASSET_RESOLVING: ArtifactType.RESOLVED_ASSETS,
            WorkflowStage.VOICE_GENERATION: ArtifactType.VOICE_MANIFEST,
            WorkflowStage.SUBTITLE_GENERATION: ArtifactType.SUBTITLE_MANIFEST,
            WorkflowStage.TIMELINE_BUILDING: ArtifactType.TIMELINE,
            WorkflowStage.RENDERING: ArtifactType.RENDER_REPORT,
            WorkflowStage.OUTPUT_VALIDATION: ArtifactType.OUTPUT_VALIDATION,
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