"""
SourceAnalyzerAgent - Analyzes and selects source content.

Responsibilities:
- Analyze source candidates for relevance and quality
- Assess copyright and reuse risk
- Select best sources for content creation
- Provide analysis and rejection reasons
"""
import logging
from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseAgent
from ..schemas import SourceAnalysisResult, SourceAnalysisItem
from ..enums import RiskLevel

logger = logging.getLogger(__name__)


class SourceAnalyzerAgent(BaseAgent):
    """Agent for analyzing and selecting source content."""
    
    @property
    def agent_name(self) -> str:
        return "SourceAnalyzerAgent"
    
    @property
    def output_schema(self) -> type[BaseModel]:
        return SourceAnalysisResult
    
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for source analysis."""
        topic = context.get("topic", "")
        target_platform = context.get("target_platform", "youtube_shorts")
        sources = context.get("sources", [])
        user_instructions = context.get("user_instructions", "")
        
        sources_text = "\n".join([
            f"- {s.get('title', 'Unknown')} ({s.get('platform', 'unknown')}): "
            f"{s.get('source_url', 'no url')}, "
            f"views: {s.get('view_count', 0)}, "
            f"likes: {s.get('like_count', 0)}"
            for s in sources
        ])
        
        prompt = f"""You are a content analyst for {target_platform}.

Your task is to analyze source candidates for the topic "{topic}" and select the best ones for content creation.

User instructions: {user_instructions}

Available sources:
{sources_text}

For each source, analyze:
- Relevance to the topic (0-1)
- Visual quality (0-1)
- Content value (0-1)
- Reuse risk (low/medium/high)
- Copyright risk (low/medium/high)
- Whether it can be downloaded
- Summary of the content
- Key claims
- Key visuals
- Rejection reasons (if rejected)

Select the best sources (max {context.get('max_sources', 5)}) and reject the rest.

Format your response as JSON:
{{
    "selected_sources": [
        {{
            "source_id": "id",
            "source_url": "url",
            "platform": "platform",
            "title": "title",
            "relevance_score": 0.8,
            "visual_quality_score": 0.7,
            "content_value_score": 0.9,
            "reuse_risk": "low",
            "copyright_risk": "low",
            "download_available": true,
            "summary": "summary",
            "key_claims": ["claim1", "claim2"],
            "key_visuals": ["visual1", "visual2"],
            "rejection_reasons": []
        }}
    ],
    "rejected_sources": [...],
    "warnings": ["warning1"]
}}
"""
        return prompt
    
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate source analysis output."""
        # If output only contains raw_content (from LLM router fallback), use agent's mock
        if set(output.keys()) == {"raw_content"}:
            logger.warning("LLM returned raw_content only, using agent mock output")
            return self._mock_output()
        
        try:
            result = self.output_schema(**output)
            return result.model_dump()
        except Exception as e:
            logger.warning(f"Output validation failed, returning default: {e}")
            return SourceAnalysisResult(
                selected_sources=[],
                rejected_sources=[],
                warnings=["Analysis failed, no sources selected"],
            ).model_dump()
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return SourceAnalysisResult(
            selected_sources=[
                SourceAnalysisItem(
                    source_id="source1",
                    source_url="https://youtube.com/watch?v=example",
                    platform="youtube",
                    title="Example Video",
                    relevance_score=0.9,
                    visual_quality_score=0.8,
                    content_value_score=0.85,
                    reuse_risk=RiskLevel.LOW,
                    copyright_risk=RiskLevel.LOW,
                    download_available=True,
                    summary="A good example video",
                    key_claims=["Claim 1", "Claim 2"],
                    key_visuals=["Visual 1"],
                ),
            ],
            rejected_sources=[],
            warnings=[],
        ).model_dump()
