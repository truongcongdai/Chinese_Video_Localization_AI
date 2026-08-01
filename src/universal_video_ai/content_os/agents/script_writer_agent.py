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
        # If output only contains raw_content (from LLM router fallback), use agent's mock
        if set(output.keys()) == {"raw_content"}:
            logger.warning("LLM returned raw_content only, using agent mock output")
            return self._mock_output()
        
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
        # Get context from the agent to make output dynamic
        context = getattr(self, '_context', {})
        topic = context.get("topic", "")
        objective = context.get("objective", "")
        target_language = context.get("target_language", "vi")
        
        # Use topic as the main content theme
        content_theme = topic if topic else "nội dung này"
        
        # Generate dynamic content based on topic (generic template)
        if target_language == "vi":
            # Vietnamese template
            return GeneratedScript(
                title_options=[
                    f"Review {content_theme}",
                    f"{content_theme} Có Gì Đặc Biệt?",
                    f"Tất Cả Về {content_theme}"
                ],
                hook=f"Bạn có muốn biết về {content_theme} không? Cùng tìm hiểu nhé!",
                narration_text=f"Hôm nay chúng ta sẽ cùng khám phá {content_theme} một cách chi tiết. Đây là những thông tin thú vị và hữu ích mà bạn cần biết về {content_theme}.",
                segments=[
                    ScriptSegment(
                        segment_id="seg1",
                        start_second=0.0,
                        end_second=5.0,
                        narration=f"Bạn có muốn biết về {content_theme} không?",
                        subtitle_text=f"Bạn có muốn biết về {content_theme} không?",
                        visual_instruction=f"Show {content_theme} intro",
                    ),
                    ScriptSegment(
                        segment_id="seg2",
                        start_second=5.0,
                        end_second=20.0,
                        narration=f"Đầu tiên, hãy cùng tìm hiểu những điểm nổi bật của {content_theme}.",
                        subtitle_text=f"Đầu tiên, hãy cùng tìm hiểu những điểm nổi bật của {content_theme}.",
                        visual_instruction=f"Show {content_theme} highlights",
                    ),
                    ScriptSegment(
                        segment_id="seg3",
                        start_second=20.0,
                        end_second=35.0,
                        narration=f"Tiếp theo, chúng ta sẽ đi sâu vào chi tiết về {content_theme}.",
                        subtitle_text=f"Tiếp theo, chúng ta sẽ đi sâu vào chi tiết về {content_theme}.",
                        visual_instruction=f"Show {content_theme} details",
                    ),
                    ScriptSegment(
                        segment_id="seg4",
                        start_second=35.0,
                        end_second=45.0,
                        narration=f"Tổng quan: Đây là những gì bạn cần biết về {content_theme}!",
                        subtitle_text=f"Tổng quan: Đây là những gì bạn cần biết về {content_theme}!",
                        visual_instruction=f"Show summary",
                    ),
                ],
                description=f"Khám phá {content_theme} - Những thông tin thú vị và hữu ích về {content_theme} mà bạn cần biết.",
                hashtags=[content_theme.lower().replace(" ", ""), "review", "info", "kienthuc", "tintuc"],
                estimated_duration_seconds=45.0,
                source_attributions=[],
            ).model_dump()
        else:
            # English template
            return GeneratedScript(
                title_options=[
                    f"{content_theme.title()} Review",
                    f"Is {content_theme} Worth It?",
                    f"Everything About {content_theme.title()}"
                ],
                hook=f"Do you want to know about {content_theme}? Let's find out!",
                narration_text=f"Today we'll explore {content_theme} in detail. Here are the interesting and useful information you need to know about {content_theme}.",
                segments=[
                    ScriptSegment(
                        segment_id="seg1",
                        start_second=0.0,
                        end_second=5.0,
                        narration=f"Do you want to know about {content_theme}?",
                        subtitle_text=f"Do you want to know about {content_theme}?",
                        visual_instruction=f"Show {content_theme} intro",
                    ),
                    ScriptSegment(
                        segment_id="seg2",
                        start_second=5.0,
                        end_second=20.0,
                        narration=f"First, let's explore the highlights of {content_theme}.",
                        subtitle_text=f"First, let's explore the highlights of {content_theme}.",
                        visual_instruction=f"Show {content_theme} highlights",
                    ),
                    ScriptSegment(
                        segment_id="seg3",
                        start_second=20.0,
                        end_second=35.0,
                        narration=f"Next, we'll dive deep into the details of {content_theme}.",
                        subtitle_text=f"Next, we'll dive deep into the details of {content_theme}.",
                        visual_instruction=f"Show {content_theme} details",
                    ),
                    ScriptSegment(
                        segment_id="seg4",
                        start_second=35.0,
                        end_second=45.0,
                        narration=f"Overall: This is what you need to know about {content_theme}!",
                        subtitle_text=f"Overall: This is what you need to know about {content_theme}!",
                        visual_instruction=f"Show summary",
                    ),
                ],
                description=f"Explore {content_theme} - Interesting and useful information about {content_theme} that you need to know.",
                hashtags=[content_theme.lower().replace(" ", ""), "review", "info", "knowledge", "news"],
                estimated_duration_seconds=45.0,
                source_attributions=[],
            ).model_dump()
