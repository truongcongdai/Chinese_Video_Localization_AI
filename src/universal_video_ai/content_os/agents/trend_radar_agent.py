"""
TrendRadarAgent - Expands topic keywords and identifies trending content.

Responsibilities:
- Expand user topic into related keywords
- Identify trending content for the topic
- Score trends by relevance and velocity
- Return structured trend candidates
"""
import logging
from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseAgent
from ..schemas import TrendRadarResult, TrendCandidate
from ..enums import WorkflowStage

logger = logging.getLogger(__name__)


class TrendRadarAgent(BaseAgent):
    """Agent for trend research and keyword expansion."""
    
    @property
    def agent_name(self) -> str:
        return "TrendRadarAgent"
    
    @property
    def output_schema(self) -> type[BaseModel]:
        return TrendRadarResult
    
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for trend research."""
        topic = context.get("topic", "")
        target_platform = context.get("target_platform", "youtube_shorts")
        target_market = context.get("target_market", "Vietnam")
        
        prompt = f"""You are a trend research specialist for {target_platform} in {target_market}.

Your task is to expand the topic "{topic}" into related keywords and identify trending content patterns.

Please provide:
1. Expanded keywords related to the topic (5-10 keywords)
2. Detected trending content patterns for this topic
3. Any warnings about the topic (e.g., seasonal, oversaturated)

Format your response as JSON with this structure:
{{
    "expanded_keywords": ["keyword1", "keyword2", ...],
    "detected_trends": [
        {{
            "title": "Trend title",
            "platform": "platform_name",
            "source_url": "url",
            "author": "author",
            "published_at": "date",
            "view_count": 1000,
            "like_count": 100,
            "comment_count": 50,
            "share_count": 20,
            "trend_score": 0.8,
            "confidence": 0.9,
            "reasoning": "Why this is trending",
            "raw_metadata": {{}}
        }}
    ],
    "warnings": ["warning1", "warning2"]
}}
"""
        return prompt
    
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate trend radar output."""
        try:
            result = self.output_schema(**output)
            return result.model_dump()
        except Exception as e:
            logger.warning(f"Output validation failed, returning default: {e}")
            # Return a valid default structure
            return TrendRadarResult(
                topic=output.get("topic", ""),
                expanded_keywords=output.get("expanded_keywords", []),
                detected_trends=[],
                warnings=output.get("warnings", []),
            ).model_dump()
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return TrendRadarResult(
            topic="cooking",
            expanded_keywords=["food", "recipe", "cooking tips", "kitchen hacks", "meal prep"],
            detected_trends=[
                TrendCandidate(
                    title="Quick 5-Minute Breakfast",
                    platform="youtube_shorts",
                    source_url="https://youtube.com/shorts/example1",
                    author="FoodChannel",
                    view_count=50000,
                    like_count=5000,
                    comment_count=200,
                    share_count=100,
                    trend_score=0.85,
                    confidence=0.9,
                    reasoning="High engagement, recent viral pattern",
                ),
            ],
            warnings=["Topic is competitive, focus on unique angle"],
        ).model_dump()
