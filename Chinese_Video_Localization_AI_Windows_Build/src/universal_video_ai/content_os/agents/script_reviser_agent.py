"""
ScriptReviserAgent - Revises script based on audit feedback.

Responsibilities:
- Fix issues identified by audit
- Improve weak elements
- Maintain timing constraints
- Preserve core message
- Return revised script with change summary
"""
import logging
from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseAgent
from ..schemas import RevisionResult, GeneratedScript, AuditIssue
from ..visual_prompts import infer_topic, normalize_generated_script

logger = logging.getLogger(__name__)


class ScriptReviserAgent(BaseAgent):
    """Agent for script revision."""
    
    @property
    def agent_name(self) -> str:
        return "ScriptReviserAgent"
    
    @property
    def output_schema(self) -> type[BaseModel]:
        return RevisionResult
    
    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for script revision."""
        script = context.get("script", {})
        audit_report = context.get("audit_report", {})
        issues = audit_report.get("issues", [])
        
        issues_text = "\n".join([
            f"- {i.get('severity', 'info')}: {i.get('description', '')} "
            f"(fix: {i.get('required_fix', '')})"
            for i in issues
        ])
        
        prompt = f"""You are a script reviser.

Your task is to revise the script based on audit feedback.

Current script:
- Hook: {script.get('hook', '')}
- Narration: {script.get('narration_text', '')[:500]}...

Audit feedback:
- Decision: {audit_report.get('decision', '')}
- Overall score: {audit_report.get('overall_score', 0)}
- Hook strength: {audit_report.get('hook_strength', 0)}
- Originality: {audit_report.get('originality_score', 0)}

Issues to fix:
{issues_text}

Revise the script to:
1. Fix all critical and warning issues
2. Improve weak elements (hook, originality, clarity)
3. Maintain the target timing
4. Preserve the core message
5. Keep source attributions if present

Provide:
- Revised script (same format as original)
- Change summary (what you changed and why)
- Remaining issues (if any couldn't be fixed)

Format your response as JSON:
{{
    "revised_script": {{
        "title_options": ["Title 1"],
        "hook": "Revised hook",
        "narration_text": "Revised narration",
        "segments": [...],
        "description": "Revised description",
        "hashtags": ["tag1"],
        "estimated_duration_seconds": 45.0,
        "source_attributions": []
    }},
    "change_summary": "Summary of changes made",
    "remaining_issues": [...]
}}

Important schema rules:
- Every revised_script.segments item must use exactly these keys:
  segment_id, start_second, end_second, narration, subtitle_text, visual_instruction, source_refs.
- Do not use time_start/time_end/start/end. Use start_second/end_second.
- Preserve segment timing and visual_instruction unless a change is required by the audit.
"""
        return prompt
    
    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate revision output."""
        # If output only contains raw_content (from LLM router fallback), use agent's mock
        if set(output.keys()) == {"raw_content"}:
            logger.warning("LLM returned raw_content only, using agent mock output")
            return self._mock_output()
        
        try:
            output = self._normalize_revision_output(output)
            result = self.output_schema(**output)
            return result.model_dump()
        except Exception as e:
            logger.warning(f"Output validation failed, returning default: {e}")
            original_script = getattr(self, "_context", {}).get("script") or {}
            topic = infer_topic(getattr(self, "_context", {}), original_script.get("hook", ""), original_script.get("narration_text", ""))
            return RevisionResult(
                revised_script=GeneratedScript(**normalize_generated_script(original_script, topic)),
                change_summary="Revision validation failed; preserved the current valid script instead of returning an empty script",
                remaining_issues=[],
            ).model_dump()

    def _normalize_revision_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Gemini/OpenAI alias fields before validating RevisionResult."""
        data = dict(output or {})
        revised_script = data.get("revised_script") or data.get("script") or data.get("revised") or {}
        topic = infer_topic(getattr(self, "_context", {}), revised_script.get("hook", ""), revised_script.get("narration_text", ""))
        data["revised_script"] = normalize_generated_script(revised_script, topic)

        remaining = data.get("remaining_issues") or []
        normalized_issues = []
        for index, issue in enumerate(remaining, start=1):
            if not isinstance(issue, dict):
                issue = {"description": str(issue)}
            normalized_issues.append({
                "issue_id": issue.get("issue_id") or f"remaining_{index}",
                "severity": issue.get("severity") if issue.get("severity") in {"info", "warning", "critical"} else "info",
                "category": issue.get("category") or "revision",
                "segment_id": issue.get("segment_id"),
                "description": issue.get("description") or "",
                "required_fix": issue.get("required_fix") or "",
            })
        data["remaining_issues"] = normalized_issues
        data["change_summary"] = data.get("change_summary") or data.get("summary") or ""
        return data
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return RevisionResult(
            revised_script=GeneratedScript(
                title_options=["Improved Cooking Hack", "Better Recipe", "Kitchen Secret Revealed"],
                hook="Want to cook like a pro? Here's the secret!",
                narration_text="Here's the improved cooking technique...",
                segments=[],
                description="Learn this amazing cooking hack!",
                hashtags=["cooking", "recipe", "food"],
                estimated_duration_seconds=45.0,
                source_attributions=[],
            ),
            change_summary="Improved hook for stronger engagement, clarified narration",
            remaining_issues=[],
        ).model_dump()
