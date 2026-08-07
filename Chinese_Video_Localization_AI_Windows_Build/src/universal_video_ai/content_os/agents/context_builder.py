"""
Context builder for Content OS agents.

Builds context for agent prompts including:
- Channel memory
- Platform skill injection
- Previous artifacts
- User instructions
"""
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from ..repository import ContentOSRepository
from ..enums import MemoryType

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Builds context for agent prompts.
    
    Context includes:
    - Channel profile and memories
    - Platform-specific skills
    - Previous workflow artifacts
    - User instructions and preferences
    """
    
    def __init__(self, repository: ContentOSRepository):
        self.repository = repository
        self.logger = logging.getLogger(__name__)
    
    def build(
        self,
        user_id: int,
        channel_key: str,
        target_platform: str,
        content_format: str,
        previous_artifacts: Optional[Dict[str, Any]] = None,
        user_instructions: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Build context for an agent.
        
        Args:
            user_id: User ID
            channel_key: Channel identifier
            target_platform: Target platform (youtube_shorts, facebook_reels, etc.)
            content_format: Content format (trend_decode, etc.)
            previous_artifacts: Dictionary of previous artifacts by type
            user_instructions: User-provided instructions
            **kwargs: Additional context
            
        Returns:
            Context dictionary
        """
        context = {
            "user_id": user_id,
            "channel_key": channel_key,
            "target_platform": target_platform,
            "content_format": content_format,
            "user_instructions": user_instructions,
            "memories": self._load_memories(user_id, channel_key),
            "platform_skills": self._load_platform_skills(target_platform),
            "format_skills": self._load_format_skills(content_format),
            "previous_artifacts": previous_artifacts or {},
        }
        
        # Add any additional context
        context.update(kwargs)
        
        self.logger.debug(
            f"Built context for {channel_key} on {target_platform}: "
            f"{len(context['memories'])} memories, "
            f"{len(context['platform_skills'])} platform skills"
        )
        
        return context
    
    def _load_memories(self, user_id: int, channel_key: str) -> Dict[str, Any]:
        """Load channel memories."""
        memories = self.repository.list_memories(
            user_id=user_id,
            channel_key=channel_key,
            active_only=True,
        )
        
        # Group by memory type
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for memory in memories:
            memory_type = memory.memory_type
            if memory_type not in grouped:
                grouped[memory_type] = []
            grouped[memory_type].append({
                "key": memory.memory_key,
                "value": memory.value,
                "confidence": memory.confidence,
                "source_run_id": memory.source_run_id,
            })
        
        return grouped
    
    def _load_platform_skills(self, platform: str) -> List[str]:
        """
        Load platform-specific skills/instructions.
        
        These are platform-specific best practices and requirements.
        """
        skills_path = Path(__file__).parent.parent / "skills" / platform
        skills = []
        
        if skills_path.exists():
            # Load skill files if they exist
            for skill_file in skills_path.glob("*.py"):
                try:
                    # Import and extract docstring or constants
                    module_name = f"universal_video_ai.content_os.skills.{platform}.{skill_file.stem}"
                    try:
                        module = __import__(module_name, fromlist=[''])
                        if hasattr(module, 'SKILLS'):
                            skills.extend(module.SKILLS)
                        elif module.__doc__:
                            skills.append(module.__doc__)
                    except ImportError:
                        pass
                except Exception as e:
                    self.logger.warning(f"Failed to load skill {skill_file}: {e}")
        
        # Default platform skills
        if not skills:
            skills = self._default_platform_skills(platform)
        
        return skills
    
    def _default_platform_skills(self, platform: str) -> List[str]:
        """Default platform skills when no skill files exist."""
        defaults = {
            "youtube_shorts": [
                "Keep videos under 60 seconds for maximum engagement",
                "Use vertical format (9:16)",
                "Start with a strong hook in the first 3 seconds",
                "Use captions for accessibility",
                "Include a call-to-action",
            ],
            "facebook_reels": [
                "Keep videos under 90 seconds",
                "Use vertical format (9:16)",
                "Leverage Facebook's music library",
                "Include trending hashtags",
                "Engage with comments",
            ],
            "tiktok": [
                "Keep videos under 60 seconds",
                "Use trending sounds and effects",
                "Post consistently at optimal times",
                "Engage with the community",
                "Use relevant hashtags",
            ],
            "instagram_reels": [
                "Keep videos under 90 seconds",
                "Use high-quality visuals",
                "Leverage Instagram's music library",
                "Use Instagram stickers and effects",
                "Cross-post to Stories",
            ],
        }
        return defaults.get(platform, [])
    
    def _load_format_skills(self, content_format: str) -> List[str]:
        """
        Load content format-specific skills.
        
        These are format-specific best practices and requirements.
        """
        skills_path = Path(__file__).parent.parent / "skills" / content_format
        skills = []
        
        if skills_path.exists():
            for skill_file in skills_path.glob("*.py"):
                try:
                    module_name = f"universal_video_ai.content_os.skills.{content_format}.{skill_file.stem}"
                    try:
                        module = __import__(module_name, fromlist=[''])
                        if hasattr(module, 'SKILLS'):
                            skills.extend(module.SKILLS)
                        elif module.__doc__:
                            skills.append(module.__doc__)
                    except ImportError:
                        pass
                except Exception as e:
                    self.logger.warning(f"Failed to load format skill {skill_file}: {e}")
        
        # Default format skills
        if not skills:
            skills = self._default_format_skills(content_format)
        
        return skills
    
    def _default_format_skills(self, content_format: str) -> List[str]:
        """Default format skills when no skill files exist."""
        defaults = {
            "trend_decode": [
                "Analyze the core trend pattern",
                "Identify the key elements that make it viral",
                "Adapt the pattern to your niche",
                "Add original value beyond just copying",
                "Ensure proper attribution",
            ],
        }
        return defaults.get(content_format, [])
