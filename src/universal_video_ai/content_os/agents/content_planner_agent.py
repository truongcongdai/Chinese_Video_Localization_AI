"""
ContentPlannerAgent - Plans content structure and angle.

Responsibilities:
- Determine content angle and approach
- Create content plan with beats
- Define target audience and core message
- Plan source usage and original value add
"""
import logging
from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseAgent
from ..schemas import ContentPlan, ContentBeat

logger = logging.getLogger(__name__)


class ContentPlannerAgent(BaseAgent):
    """Agent for content planning."""
    
    @property
    def agent_name(self) -> str:
        return "ContentPlannerAgent"
    
    @property
    def output_schema(self) -> type[BaseModel]:
        return ContentPlan
    
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for content planning."""
        topic = context.get("topic", "")
        target_platform = context.get("target_platform", "youtube_shorts")
        target_duration = context.get("target_duration_seconds", 45)
        selected_sources = context.get("selected_sources", [])
        user_instructions = context.get("user_instructions", "")
        platform_skills = context.get("platform_skills", [])
        format_skills = context.get("format_skills", [])
        
        sources_text = "\n".join([f"- {s.get('title', 'Unknown')}" for s in selected_sources])
        skills_text = "\n".join([f"- {skill}" for skill in platform_skills + format_skills])
        
        prompt = f"""You are a content planner for {target_platform}.

Your task is to create a content plan for the topic "{topic}".

Target duration: {target_duration} seconds
Platform skills to follow:
{skills_text}

Available sources:
{sources_text}

User instructions: {user_instructions}

Create a content plan that includes:
- Content angle (how you'll approach the topic)
- Target platforms
- Target audience
- Core message
- Hook (first 3 seconds)
- Content beats (timeline of what happens when)
- Must-include elements
- Must-avoid elements
- How you'll use the sources
- Original value you'll add beyond the sources
- Call-to-action

Format your response as JSON:
{{
    "content_angle": "angle description",
    "target_platforms": ["platform1"],
    "target_duration_seconds": 45,
    "target_audience": "audience description",
    "core_message": "main message",
    "hook": "hook text",
    "beats": [
        {{
            "order": 1,
            "start_second": 0.0,
            "end_second": 3.0,
            "purpose": "hook",
            "narration_goal": "what narration does",
            "visual_goal": "what visuals show"
        }}
    ],
    "must_include": ["element1"],
    "must_avoid": ["element1"],
    "source_usage_plan": ["how to use source1"],
    "original_value_add": ["what you add"],
    "call_to_action": "CTA text"
}}
"""
        return prompt
    
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate content plan output."""
        # If output only contains raw_content (from LLM router fallback), use agent's mock
        if set(output.keys()) == {"raw_content"}:
            logger.warning("LLM returned raw_content only, using agent mock output")
            return self._mock_output()
        
        try:
            result = self.output_schema(**output)
            return result.model_dump()
        except Exception as e:
            logger.warning(f"Output validation failed, returning default: {e}")
            return ContentPlan(
                content_angle="Educational",
                target_platforms=["youtube_shorts"],
                target_duration_seconds=45,
                target_audience="General audience",
                core_message="Main message",
                hook="Hook text",
                beats=[
                    ContentBeat(
                        order=1,
                        start_second=0.0,
                        end_second=3.0,
                        purpose="hook",
                        narration_goal="Grab attention",
                        visual_goal="Show something interesting",
                    ),
                ],
                must_include=[],
                must_avoid=[],
                source_usage_plan=[],
                original_value_add=[],
                call_to_action="Subscribe",
            ).model_dump()
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return ContentPlan(
            content_angle="Educational tutorial",
            target_platforms=["youtube_shorts"],
            target_duration_seconds=45,
            target_audience="Beginners",
            core_message="Learn this skill in 45 seconds",
            hook="Want to master this skill?",
            beats=[
                ContentBeat(
                    order=1,
                    start_second=0.0,
                    end_second=3.0,
                    purpose="hook",
                    narration_goal="Grab attention",
                    visual_goal="Show result",
                ),
                ContentBeat(
                    order=2,
                    start_second=3.0,
                    end_second=15.0,
                    purpose="explanation",
                    narration_goal="Explain the concept",
                    visual_goal="Show demonstration",
                ),
            ],
            must_include=["Clear explanation", "Visual demonstration"],
            must_avoid=["Jargon", "Long pauses"],
            source_usage_plan=["Use source as reference"],
            original_value_add=["Simplify explanation", "Add practical tips"],
            call_to_action="Follow for more tips",
        ).model_dump()
