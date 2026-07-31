"""
Storyboard system for Content OS.

Manages visual planning and human editing workflows for video content.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import json
import time


class StoryboardStatus(str, Enum):
    """Storyboard workflow status."""
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class StoryboardScene:
    """A single scene in the storyboard."""
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
    assets: List[str] = field(default_factory=list)


@dataclass
class Storyboard:
    """Complete storyboard for a video."""
    id: Optional[int]
    run_id: int
    user_id: int
    version: int
    status: StoryboardStatus
    scenes: List[StoryboardScene]
    total_duration: float
    created_at: float
    updated_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def scene_count(self) -> int:
        return len(self.scenes)


class StoryboardManager:
    """
    Manages storyboard creation, editing, and approval workflows.
    
    Features:
    - Generate storyboard from script
    - Human editing interface
    - Approval workflow
    - Version tracking
    """
    
    def __init__(self, repository):
        self.repository = repository
    
    def create_from_script(
        self,
        run_id: int,
        user_id: int,
        script: Dict[str, Any],
    ) -> Storyboard:
        """
        Create a storyboard from a generated script.
        
        Args:
            run_id: Run ID
            user_id: User ID
            script: Generated script data
        
        Returns:
            Created storyboard
        """
        scenes = []
        segments = script.get("segments", [])
        
        for i, segment in enumerate(segments):
            scene = StoryboardScene(
                scene_id=f"scene_{i+1}",
                order=i + 1,
                start_second=segment.get("start_second", 0.0),
                end_second=segment.get("end_second", 0.0),
                visual_instruction=segment.get("visual_instruction", ""),
                subtitle_text=segment.get("subtitle_text", ""),
                narration_text=segment.get("narration", ""),
                camera_angle="front",
                transition="cut",
                notes="",
                assets=[],
            )
            scenes.append(scene)
        
        total_duration = script.get("estimated_duration_seconds", 45.0)
        
        storyboard = Storyboard(
            id=None,
            run_id=run_id,
            user_id=user_id,
            version=1,
            status=StoryboardStatus.DRAFT,
            scenes=scenes,
            total_duration=total_duration,
            created_at=time.time(),
            updated_at=time.time(),
            metadata={
                "source_script_id": script.get("script_id", ""),
                "title_options": script.get("title_options", []),
            },
        )
        
        # Store as artifact
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def update_scene(
        self,
        run_id: int,
        user_id: int,
        scene_id: str,
        updates: Dict[str, Any],
    ) -> Storyboard:
        """
        Update a specific scene in the storyboard.
        
        Args:
            run_id: Run ID
            user_id: User ID
            scene_id: Scene ID to update
            updates: Fields to update
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        # Find and update scene
        for scene in storyboard.scenes:
            if scene.scene_id == scene_id:
                for key, value in updates.items():
                    if hasattr(scene, key):
                        setattr(scene, key, value)
                break
        
        storyboard.updated_at = time.time()
        storyboard.version += 1
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def add_scene(
        self,
        run_id: int,
        user_id: int,
        scene: StoryboardScene,
        after_scene_id: Optional[str] = None,
    ) -> Storyboard:
        """
        Add a new scene to the storyboard.
        
        Args:
            run_id: Run ID
            user_id: User ID
            scene: Scene to add
            after_scene_id: Insert after this scene ID (None = append)
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        if after_scene_id is None:
            # Append at end
            scene.order = len(storyboard.scenes) + 1
            storyboard.scenes.append(scene)
        else:
            # Insert after specified scene
            insert_index = 0
            for i, s in enumerate(storyboard.scenes):
                if s.scene_id == after_scene_id:
                    insert_index = i + 1
                    break
            scene.order = insert_index + 1
            storyboard.scenes.insert(insert_index, scene)
            # Reorder remaining scenes
            for i in range(insert_index + 1, len(storyboard.scenes)):
                storyboard.scenes[i].order = i + 1
        
        storyboard.updated_at = time.time()
        storyboard.version += 1
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def delete_scene(
        self,
        run_id: int,
        user_id: int,
        scene_id: str,
    ) -> Storyboard:
        """
        Delete a scene from the storyboard.
        
        Args:
            run_id: Run ID
            user_id: User ID
            scene_id: Scene ID to delete
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        storyboard.scenes = [s for s in storyboard.scenes if s.scene_id != scene_id]
        
        # Reorder remaining scenes
        for i, scene in enumerate(storyboard.scenes):
            scene.order = i + 1
        
        storyboard.updated_at = time.time()
        storyboard.version += 1
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def reorder_scenes(
        self,
        run_id: int,
        user_id: int,
        scene_order: List[str],
    ) -> Storyboard:
        """
        Reorder scenes in the storyboard.
        
        Args:
            run_id: Run ID
            user_id: User ID
            scene_order: List of scene IDs in new order
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        # Create scene map
        scene_map = {s.scene_id: s for s in storyboard.scenes}
        
        # Rebuild scenes in new order
        new_scenes = []
        for i, scene_id in enumerate(scene_order):
            if scene_id in scene_map:
                scene = scene_map[scene_id]
                scene.order = i + 1
                new_scenes.append(scene)
        
        storyboard.scenes = new_scenes
        storyboard.updated_at = time.time()
        storyboard.version += 1
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def submit_for_review(
        self,
        run_id: int,
        user_id: int,
    ) -> Storyboard:
        """
        Submit storyboard for review.
        
        Args:
            run_id: Run ID
            user_id: User ID
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        storyboard.status = StoryboardStatus.IN_REVIEW
        storyboard.updated_at = time.time()
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def approve_storyboard(
        self,
        run_id: int,
        user_id: int,
        approver_id: int,
        notes: str = "",
    ) -> Storyboard:
        """
        Approve the storyboard.
        
        Args:
            run_id: Run ID
            user_id: User ID
            approver_id: ID of the approver
            notes: Approval notes
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        storyboard.status = StoryboardStatus.APPROVED
        storyboard.updated_at = time.time()
        storyboard.metadata["approved_by"] = approver_id
        storyboard.metadata["approval_notes"] = notes
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def reject_storyboard(
        self,
        run_id: int,
        user_id: int,
        approver_id: int,
        reason: str,
    ) -> Storyboard:
        """
        Reject the storyboard.
        
        Args:
            run_id: Run ID
            user_id: User ID
            approver_id: ID of the approver
            reason: Rejection reason
        
        Returns:
            Updated storyboard
        """
        storyboard = self._get_storyboard(run_id, user_id)
        if not storyboard:
            raise ValueError(f"Storyboard not found for run {run_id}")
        
        storyboard.status = StoryboardStatus.REJECTED
        storyboard.updated_at = time.time()
        storyboard.metadata["rejected_by"] = approver_id
        storyboard.metadata["rejection_reason"] = reason
        
        self._store_storyboard(storyboard)
        
        return storyboard
    
    def _get_storyboard(
        self, run_id: int, user_id: int
    ) -> Optional[Storyboard]:
        """Get storyboard for a run."""
        # Try to get from artifacts
        artifacts = self.repository.list_artifacts(run_id)
        
        for artifact in artifacts:
            if artifact.artifact_type == "storyboard":
                try:
                    data = artifact.metadata if hasattr(artifact, 'metadata') else {}
                    if data:
                        # Convert scene dicts back to StoryboardScene objects
                        scene_data = data.get("scenes", [])
                        scenes = [
                            StoryboardScene(**s) if isinstance(s, dict) else s
                            for s in scene_data
                        ]
                        data["scenes"] = scenes
                        # Convert status string back to enum
                        status_str = data.get("status")
                        if isinstance(status_str, str):
                            data["status"] = StoryboardStatus(status_str)
                        return Storyboard(**data)
                except (TypeError, KeyError, ValueError):
                    continue
        
        return None
    
    def _store_storyboard(self, storyboard: Storyboard):
        """Store storyboard as artifact."""
        # Convert to dict
        data = {
            "id": storyboard.id,
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
        
        # Store as artifact
        self.repository.create_artifact(
            run_id=storyboard.run_id,
            user_id=storyboard.user_id,
            artifact_type="storyboard",
            version=storyboard.version,
            schema_version="1.0",
            path=f"/storyboards/{storyboard.run_id}_v{storyboard.version}.json",
            checksum="",
            metadata=data,
            created_by_agent="StoryboardManager",
        )
