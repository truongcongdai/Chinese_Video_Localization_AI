"""
Context compiler for Content OS.

Compiles comprehensive context from channel settings, project configuration,
memory system, and previous artifacts for use by agents.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from .enums import WorkflowStage, ArtifactType
from .repository import ContentOSRepository


@dataclass
class CompiledContext:
    """Compiled context for agent execution."""
    # User and project info
    user_id: int
    project_id: int
    run_id: int
    channel_id: Optional[int]
    
    # Channel configuration
    channel_name: str
    platforms: List[str]
    niche: str
    target_audience: str
    target_market: str
    target_language: str
    tone: str
    visual_identity: Dict[str, Any]
    content_rules: List[str]
    forbidden_topics: List[str]
    preferred_formats: List[str]
    
    # Project configuration
    mode: str
    topic: str
    objective: str
    target_platform: str
    target_duration_seconds: int
    content_style: str
    visual_style: str
    voice_id: str
    subtitle_style_id: str
    background_music_enabled: bool
    user_instructions: str
    
    # Memory context
    relevant_memories: List[Dict[str, Any]]
    
    # Previous artifacts (if any)
    previous_scripts: List[Dict[str, Any]]
    previous_storyboards: List[Dict[str, Any]]
    
    # Current workflow state
    current_stage: WorkflowStage
    revision_count: int
    
    # Additional context
    settings: Dict[str, Any]


class ContextCompiler:
    """
    Compiles context from multiple sources for agent execution.
    
    Sources:
    - Channel configuration (branding, audience, rules)
    - Project configuration (topic, style, constraints)
    - Memory system (learned patterns, preferences)
    - Previous artifacts (scripts, storyboards)
    - Current workflow state
    """
    
    def __init__(self, repository: ContentOSRepository):
        self.repository = repository
    
    def compile(
        self,
        user_id: int,
        project_id: int,
        run_id: int,
        stage: WorkflowStage,
    ) -> CompiledContext:
        """
        Compile comprehensive context for agent execution.
        
        Args:
            user_id: User ID
            project_id: Project ID
            run_id: Run ID
            stage: Current workflow stage
        
        Returns:
            CompiledContext with all relevant information
        """
        # Get project
        project = self.repository.get_project(project_id, user_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        # Get run
        run = self.repository.get_run(run_id, user_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        
        # Get channel if available
        channel = None
        if project.channel_id:
            channel = self.repository.get_channel(project.channel_id, user_id)
        
        # Compile channel context
        channel_context = self._compile_channel_context(channel, project)
        
        # Compile project context
        project_context = self._compile_project_context(project)
        
        # Compile memory context
        memory_context = self._compile_memory_context(
            user_id, project.channel_name, stage
        )
        
        # Compile previous artifacts
        artifacts_context = self._compile_artifacts_context(run_id, user_id)
        
        return CompiledContext(
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            channel_id=project.channel_id,
            **channel_context,
            **project_context,
            relevant_memories=memory_context,
            previous_scripts=artifacts_context["scripts"],
            previous_storyboards=artifacts_context["storyboards"],
            current_stage=WorkflowStage(run.current_stage),
            revision_count=run.revision_count,
            settings=project.settings,
        )
    
    def _compile_channel_context(
        self, channel: Optional[Any], project: Any
    ) -> Dict[str, Any]:
        """Compile channel configuration context."""
        if channel:
            return {
                "channel_name": channel.channel_name,
                "platforms": channel.platforms,
                "niche": channel.niche or "",
                "target_audience": channel.target_audience or "",
                "target_market": channel.target_market,
                "target_language": channel.default_language,
                "tone": channel.tone or "professional",
                "visual_identity": channel.visual_identity,
                "content_rules": channel.content_rules,
                "forbidden_topics": channel.forbidden_topics,
                "preferred_formats": channel.preferred_formats,
            }
        else:
            # Fallback to project channel_name
            return {
                "channel_name": project.channel_name,
                "platforms": [project.target_platform],
                "niche": "",
                "target_audience": "",
                "target_market": "Vietnam",
                "target_language": project.target_language,
                "tone": "professional",
                "visual_identity": {},
                "content_rules": [],
                "forbidden_topics": [],
                "preferred_formats": [],
            }
    
    def _compile_project_context(self, project: Any) -> Dict[str, Any]:
        """Compile project configuration context."""
        return {
            "mode": project.mode,
            "topic": project.topic,
            "objective": project.objective or "",
            "target_platform": project.target_platform,
            "target_duration_seconds": project.target_duration_seconds,
            "content_style": project.content_style or "trend_decode",
            "visual_style": project.visual_style or "modern_documentary",
            "voice_id": project.voice_id or "",
            "subtitle_style_id": project.subtitle_style_id or "",
            "background_music_enabled": project.background_music_enabled,
            "user_instructions": project.user_instructions or "",
        }
    
    def _compile_memory_context(
        self, user_id: int, channel_name: str, stage: WorkflowStage
    ) -> List[Dict[str, Any]]:
        """Compile relevant memories for the current stage."""
        memories = self.repository.list_memories(
            user_id=user_id,
            channel_key=channel_name,
            active_only=True,
        )
        
        # Filter memories by relevance to stage
        stage_memory_types = self._get_memory_types_for_stage(stage)
        relevant = [
            {
                "memory_type": m.memory_type,
                "memory_key": m.memory_key,
                "value": m.value,
                "confidence": m.confidence,
            }
            for m in memories
            if m.memory_type in stage_memory_types
        ]
        
        return relevant
    
    def _get_memory_types_for_stage(self, stage: WorkflowStage) -> List[str]:
        """Get relevant memory types for a workflow stage."""
        mapping = {
            WorkflowStage.RESEARCHING: ["trend_patterns", "audience_preferences"],
            WorkflowStage.CONTENT_PLANNING: ["content_structure", "successful_formats"],
            WorkflowStage.SCRIPT_WRITING: ["tone_patterns", "language_style"],
            WorkflowStage.SCRIPT_AUDITING: ["quality_criteria", "common_issues"],
            WorkflowStage.ASSET_RESOLVING: ["asset_preferences", "visual_style"],
            WorkflowStage.VOICE_GENERATION: ["voice_preferences"],
        }
        return mapping.get(stage, [])
    
    def _compile_artifacts_context(
        self, run_id: int, user_id: int
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Compile previous artifacts from this run."""
        artifacts = self.repository.list_artifacts(run_id)
        
        scripts = []
        storyboards = []
        
        for artifact in artifacts:
            if artifact.artifact_type == ArtifactType.SCRIPT:
                scripts.append({
                    "version": artifact.version,
                    "path": artifact.path,
                    "created_at": artifact.created_at,
                })
            elif artifact.artifact_type == ArtifactType.STORYBOARD:
                storyboards.append({
                    "version": artifact.version,
                    "path": artifact.path,
                    "created_at": artifact.created_at,
                })
        
        return {
            "scripts": scripts,
            "storyboards": storyboards,
        }
    
    def format_for_agent(self, context: CompiledContext) -> str:
        """
        Format compiled context as a structured string for LLM agents.
        
        Args:
            context: Compiled context
        
        Returns:
            Formatted context string
        """
        sections = []
        
        # Channel info
        sections.append(f"## Channel: {context.channel_name}")
        sections.append(f"- Platforms: {', '.join(context.platforms)}")
        sections.append(f"- Target Audience: {context.target_audience}")
        sections.append(f"- Target Market: {context.target_market}")
        sections.append(f"- Language: {context.target_language}")
        sections.append(f"- Tone: {context.tone}")
        
        if context.content_rules:
            sections.append(f"- Content Rules: {', '.join(context.content_rules)}")
        
        if context.forbidden_topics:
            sections.append(f"- Forbidden Topics: {', '.join(context.forbidden_topics)}")
        
        # Project info
        sections.append(f"\n## Project: {context.topic}")
        sections.append(f"- Objective: {context.objective}")
        sections.append(f"- Target Platform: {context.target_platform}")
        sections.append(f"- Duration: {context.target_duration_seconds}s")
        sections.append(f"- Content Style: {context.content_style}")
        sections.append(f"- Visual Style: {context.visual_style}")
        
        if context.user_instructions:
            sections.append(f"- Instructions: {context.user_instructions}")
        
        # Memory context
        if context.relevant_memories:
            sections.append("\n## Learned Patterns")
            for memory in context.relevant_memories:
                sections.append(f"- {memory['memory_key']}: {memory['value']}")
        
        # Previous work
        if context.previous_scripts:
            sections.append(f"\n## Previous Scripts ({len(context.previous_scripts)} versions)")
        
        if context.revision_count > 0:
            sections.append(f"\n## Revision Count: {context.revision_count}")
        
        return "\n".join(sections)
