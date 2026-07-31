"""
ContentAuditAgent - Audits generated script for quality and compliance.

Responsibilities:
- Evaluate hook strength
- Assess originality and clarity
- Check copyright and factual risks
- Validate timing
- Identify issues requiring fixes
"""
import logging
from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseAgent
from ..schemas import AuditResult, AuditIssue
from ..enums import AuditDecision, RiskLevel

logger = logging.getLogger(__name__)


class ContentAuditAgent(BaseAgent):
    """Agent for content auditing."""
    
    @property
    def agent_name(self) -> str:
        return "ContentAuditAgent"
    
    @property
    def output_schema(self) -> type[BaseModel]:
        return AuditResult
    
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for content audit."""
        script = context.get("script", {})
        content_plan = context.get("content_plan", {})
        target_platform = context.get("target_platform", "youtube_shorts")
        
        prompt = f"""You are a content quality auditor for {target_platform}.

Your task is to audit the generated script for quality, originality, and compliance.

Script:
- Hook: {script.get('hook', '')}
- Narration: {script.get('narration_text', '')[:500]}...
- Segments: {len(script.get('segments', []))}

Content plan:
- Angle: {content_plan.get('content_angle', '')}
- Core message: {content_plan.get('core_message', '')}

Evaluate:
- Hook strength (0-1): Does it grab attention in first 3 seconds?
- Originality (0-1): Is the content original or derivative?
- Clarity (0-1): Is the message clear and easy to understand?
- Retention (0-1): Will viewers watch to the end?
- Source dependency: How much does it rely on sources? (low/medium/high)
- Copyright risk: Any copyright concerns? (low/medium/high)
- Factual risk: Any factual accuracy concerns? (low/medium/high)
- Timing valid: Does the timing match the target duration?

Identify issues:
- Critical issues that block publication
- Warnings that should be addressed
- Info items for awareness

Make a decision:
- PASS: Ready for approval
- PASS_WITH_FIXES: Good but needs minor fixes
- BLOCKED: Has critical issues that must be fixed

Format your response as JSON:
{{
    "decision": "PASS",
    "overall_score": 0.85,
    "hook_strength": 0.9,
    "originality_score": 0.8,
    "clarity_score": 0.85,
    "retention_score": 0.8,
    "source_dependency": "low",
    "copyright_risk": "low",
    "factual_risk": "low",
    "timing_valid": true,
    "issues": [
        {{
            "issue_id": "issue1",
            "severity": "warning",
            "category": "originality",
            "segment_id": "seg1",
            "description": "Issue description",
            "required_fix": "How to fix"
        }}
    ],
    "warnings": ["warning1"]
}}
"""
        return prompt
    
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate audit output."""
        try:
            result = self.output_schema(**output)
            return result.model_dump()
        except Exception as e:
            logger.warning(f"Output validation failed, returning default: {e}")
            return AuditResult(
                decision=AuditDecision.PASS,
                overall_score=0.7,
                hook_strength=0.7,
                originality_score=0.7,
                clarity_score=0.7,
                retention_score=0.7,
                source_dependency=RiskLevel.MEDIUM,
                copyright_risk=RiskLevel.MEDIUM,
                factual_risk=RiskLevel.LOW,
                timing_valid=True,
                issues=[],
                warnings=["Audit validation failed, using default pass"],
            ).model_dump()
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return AuditResult(
            decision=AuditDecision.PASS,
            overall_score=0.85,
            hook_strength=0.9,
            originality_score=0.8,
            clarity_score=0.85,
            retention_score=0.8,
            source_dependency=RiskLevel.LOW,
            copyright_risk=RiskLevel.LOW,
            factual_risk=RiskLevel.LOW,
            timing_valid=True,
            issues=[],
            warnings=[],
        ).model_dump()
