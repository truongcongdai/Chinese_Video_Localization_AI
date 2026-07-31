"""
ScriptWriterAgent - Generates the final script.

Responsibilities:
- Generate narration script based on content plan
- Create title options
- Write description and hashtags
- Ensure timing matches beats
- Add source attributions
"""
import logging
from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseAgent
from ..schemas import GeneratedScript, ScriptSegment

logger = logging.getLogger(__name__)


class ScriptWriterAgent(BaseAgent):
    """Agent for script generation."""
    
    @property
    def agent_name(self) -> str:
        return "ScriptWriterAgent"
    
    @property
    def output_schema(self) -> type[BaseModel]:
        return GeneratedScript
    
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for script writing."""
        content_plan = context.get("content_plan", {})
        target_language = context.get("target_language", "vi")
        target_duration = context.get("target_duration_seconds", 45)
        user_instructions = context.get("user_instructions", "")
        
        beats_text = "\n".join([
            f"- Beat {b.get('order', 0)}: {b.get('start_second', 0)}-{b.get('end_second', 0)}s - "
            f"{b.get('purpose', '')}: {b.get('narration_goal', '')}"
            for b in content_plan.get("beats", [])
        ])
        
        prompt = f"""You are a script writer for short-form video content.

Your task is to generate a script based on the content plan.

Target language: {target_language}
Target duration: {target_duration} seconds

Content plan:
- Angle: {content_plan.get('content_angle', '')}
- Hook: {content_plan.get('hook', '')}
- Core message: {content_plan.get('core_message', '')}
- Target audience: {content_plan.get('target_audience', '')}

Beats:
{beats_text}

User instructions: {user_instructions}

Generate:
- 3-5 title options (catchy, relevant)
- Hook text (first 3 seconds)
- Full narration text
- Script segments with timing matching the beats
- Description for the video
- Relevant hashtags
- Source attributions (if using sources)

Format your response as JSON:
{{
    "title_options": ["Title 1", "Title 2", "Title 3"],
    "hook": "Hook text",
    "narration_text": "Full narration",
    "segments": [
        {{
            "segment_id": "seg1",
            "start_second": 0.0,
            "end_second": 3.0,
            "narration": "narration for this segment",
            "subtitle_text": "subtitle text",
            "visual_instruction": "what to show",
            "source_refs": ["source1"]
        }}
    ],
    "description": "Video description",
    "hashtags": ["tag1", "tag2"],
    "estimated_duration_seconds": 45.0,
    "source_attributions": ["Source: @creator"]
}}
"""
        return prompt
    
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate script output."""
        try:
            result = self.output_schema(**output)
            return result.model_dump()
        except Exception as e:
            logger.warning(f"Output validation failed, returning default: {e}")
            return GeneratedScript(
                title_options=["Title 1", "Title 2", "Title 3"],
                hook="Hook text",
                narration_text="Full narration text",
                segments=[
                    ScriptSegment(
                        segment_id="seg1",
                        start_second=0.0,
                        end_second=3.0,
                        narration="Narration",
                        subtitle_text="Subtitle",
                        visual_instruction="Visual",
                    ),
                ],
                description="Description",
                hashtags=["tag1", "tag2"],
                estimated_duration_seconds=45.0,
                source_attributions=[],
            ).model_dump()
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return GeneratedScript(
            title_options=["Amazing Cooking Hack", "5-Minute Recipe", "Kitchen Secret"],
            hook="Want to cook like a pro in 5 minutes?",
            narration_text="Here's how to master this cooking technique...",
            segments=[
                ScriptSegment(
                    segment_id="seg1",
                    start_second=0.0,
                    end_second=3.0,
                    narration="Want to cook like a pro?",
                    subtitle_text="Want to cook like a pro?",
                    visual_instruction="Show final dish",
                ),
                ScriptSegment(
                    segment_id="seg2",
                    start_second=3.0,
                    end_second=45.0,
                    narration="Here's the secret technique...",
                    subtitle_text="Here's the secret technique...",
                    visual_instruction="Show cooking process",
                ),
            ],
            description="Learn this amazing cooking hack in just 5 minutes!",
            hashtags=["cooking", "recipe", "food", "kitchen", "hack"],
            estimated_duration_seconds=45.0,
            source_attributions=["Inspired by @foodchannel"],
        ).model_dump()
