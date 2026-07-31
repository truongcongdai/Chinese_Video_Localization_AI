"""
Content OS Quality Gate System.

Provides quality checks and validation gates for Content OS workflows,
ensuring content meets quality standards before proceeding to production.
"""
from typing import Optional, Dict, Any, List
from enum import Enum
import re

from .schemas import AuditResult, ContentPlan, GeneratedScript


class QualityLevel(Enum):
    """Quality level thresholds."""
    EXCELLENT = 0.9
    GOOD = 0.8
    ACCEPTABLE = 0.7
    POOR = 0.6
    UNACCEPTABLE = 0.0


class GateDecision(Enum):
    """Quality gate decision."""
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class QualityGate:
    """
    Quality gate for Content OS workflows.
    
    Validates content against quality standards at various stages
    of the workflow to ensure only high-quality content proceeds.
    """
    
    def __init__(
        self,
        min_quality_score: float = 0.7,
        strict_mode: bool = False,
    ):
        """
        Initialize the quality gate.
        
        Args:
            min_quality_score: Minimum acceptable quality score (0-1)
            strict_mode: If True, enforce stricter quality standards
        """
        self.min_quality_score = min_quality_score
        self.strict_mode = strict_mode
    
    def evaluate_content_plan(
        self,
        content_plan: ContentPlan,
        project_settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Evaluate a content plan for quality.
        
        Args:
            content_plan: Content plan to evaluate
            project_settings: Project requirements
            
        Returns:
            Evaluation result with decision and issues
        """
        issues = []
        score = 1.0
        
        # Check for required fields
        if not content_plan.title or len(content_plan.title) < 5:
            issues.append({
                "severity": "medium",
                "category": "completeness",
                "message": "Content plan title is too short or missing",
            })
            score -= 0.1
        
        if not content_plan.objective:
            issues.append({
                "severity": "high",
                "category": "completeness",
                "message": "Content plan objective is missing",
            })
            score -= 0.2
        
        if not content_plan.target_audience:
            issues.append({
                "severity": "medium",
                "category": "completeness",
                "message": "Target audience definition is missing",
            })
            score -= 0.1
        
        # Check alignment with project settings
        if content_plan.target_duration != project_settings.get("target_duration_seconds"):
            issues.append({
                "severity": "low",
                "category": "alignment",
                "message": f"Content plan duration ({content_plan.target_duration}) "
                          f"doesn't match project setting ({project_settings.get('target_duration_seconds')})",
            })
            score -= 0.05
        
        # Check for actionable guidance
        if not content_plan.visual_direction or not content_plan.audio_direction:
            issues.append({
                "severity": "medium",
                "category": "actionability",
                "message": "Visual or audio direction is missing",
            })
            score -= 0.1
        
        # Determine decision
        decision = self._make_decision(score, issues)
        
        return {
            "decision": decision.value,
            "score": max(0, score),
            "issues": issues,
            "recommendations": self._generate_recommendations(issues),
        }
    
    def evaluate_script(
        self,
        script: GeneratedScript,
        content_plan: ContentPlan,
        project_settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Evaluate a script for quality.
        
        Args:
            script: Script to evaluate
            content_plan: Content plan the script should follow
            project_settings: Project requirements
            
        Returns:
            Evaluation result with decision and issues
        """
        issues = []
        score = 1.0
        
        # Check script length
        target_duration = project_settings.get("target_duration_seconds", 45)
        estimated_duration = script.estimated_duration or 0
        
        duration_diff = abs(estimated_duration - target_duration)
        if duration_diff > target_duration * 0.2:  # More than 20% off
            issues.append({
                "severity": "high",
                "category": "timing",
                "message": f"Script duration ({estimated_duration}s) "
                          f"deviates significantly from target ({target_duration}s)",
            })
            score -= 0.2
        elif duration_diff > target_duration * 0.1:  # More than 10% off
            issues.append({
                "severity": "medium",
                "category": "timing",
                "message": f"Script duration ({estimated_duration}s) "
                          f"slightly off from target ({target_duration}s)",
            })
            score -= 0.1
        
        # Check for segments
        if not script.segments or len(script.segments) < 3:
            issues.append({
                "severity": "high",
                "category": "structure",
                "message": "Script has insufficient segments (need at least 3)",
            })
            score -= 0.2
        
        # Check segment quality
        if script.segments:
            for i, segment in enumerate(script.segments):
                if not segment.text or len(segment.text) < 5:
                    issues.append({
                        "severity": "medium",
                        "category": "content",
                        "message": f"Segment {i+1} has insufficient text",
                    })
                    score -= 0.05
        
        # Check word count
        word_count = script.total_word_count or 0
        if word_count < 50:
            issues.append({
                "severity": "medium",
                "category": "content",
                "message": f"Script has very low word count ({word_count})",
            })
            score -= 0.1
        elif word_count > 300:
            issues.append({
                "severity": "medium",
                "category": "content",
                "message": f"Script has very high word count ({word_count}) - may be too long",
            })
            score -= 0.1
        
        # Check for hook quality
        if script.segments:
            first_segment = script.segments[0]
            if not self._is_good_hook(first_segment.text):
                issues.append({
                    "severity": "medium",
                    "category": "engagement",
                    "message": "Opening hook may not be engaging enough",
                })
                score -= 0.1
        
        # Check alignment with content plan
        if content_plan.key_message:
            script_text = " ".join([s.text for s in script.segments])
            if content_plan.key_message.lower() not in script_text.lower():
                issues.append({
                    "severity": "low",
                    "category": "alignment",
                    "message": "Script may not clearly convey the key message from content plan",
                })
                score -= 0.05
        
        # Check for inappropriate content
        inappropriate = self._check_inappropriate_content(script)
        if inappropriate:
            issues.append({
                "severity": "high",
                "category": "safety",
                "message": f"Potentially inappropriate content detected: {', '.join(inappropriate)}",
            })
            score -= 0.3
        
        # Determine decision
        decision = self._make_decision(score, issues)
        
        return {
            "decision": decision.value,
            "score": max(0, score),
            "issues": issues,
            "recommendations": self._generate_recommendations(issues),
        }
    
    def evaluate_audit_result(
        self,
        audit_result: AuditResult,
    ) -> Dict[str, Any]:
        """
        Evaluate an audit result for quality gate decision.
        
        Args:
            audit_result: Audit result from Content Audit Agent
            
        Returns:
            Quality gate decision
        """
        issues = []
        score = audit_result.overall_quality
        
        # Check if audit decision aligns with quality score
        if audit_result.decision == "approved" and score < self.min_quality_score:
            issues.append({
                "severity": "high",
                "category": "consistency",
                "message": "Audit approved content below minimum quality threshold",
            })
            score -= 0.1
        
        if audit_result.decision == "needs_revision" and score >= 0.9:
            issues.append({
                "severity": "low",
                "category": "consistency",
                "message": "Audit requested revision for high-quality content",
            })
        
        # Check for high-severity issues
        high_severity_issues = [
            i for i in audit_result.issues if i.get("severity") == "high"
        ]
        if high_severity_issues:
            issues.append({
                "severity": "high",
                "category": "quality",
                "message": f"Audit found {len(high_severity_issues)} high-severity issues",
            })
            score -= 0.2
        
        # Determine decision
        decision = self._make_decision(score, issues)
        
        return {
            "decision": decision.value,
            "score": max(0, score),
            "issues": issues,
            "recommendations": self._generate_recommendations(issues),
        }
    
    def _is_good_hook(self, text: str) -> bool:
        """
        Check if text makes a good hook.
        
        Args:
            text: Hook text to evaluate
            
        Returns:
            True if text is likely a good hook
        """
        # Good hooks often:
        # - Start with a question
        # - Make a surprising statement
        # - Promise value immediately
        # - Use strong emotional words
        
        question_patterns = [r"^\?", r"^Did you", r"^Have you", r"^Can you", r"^Why"]
        surprising_patterns = [r"shocking", r"unbelievable", r"never", r"secret", r"amazing"]
        value_patterns = [r"learn", r"discover", r"find out", r"how to", r"tips"]
        
        text_lower = text.lower()
        
        for pattern in question_patterns:
            if re.search(pattern, text_lower):
                return True
        
        for pattern in surprising_patterns:
            if pattern in text_lower:
                return True
        
        for pattern in value_patterns:
            if pattern in text_lower:
                return True
        
        # Check length - good hooks are concise
        if len(text.split()) <= 10:
            return True
        
        return False
    
    def _check_inappropriate_content(self, script: GeneratedScript) -> List[str]:
        """
        Check for potentially inappropriate content.
        
        Args:
            script: Script to check
            
        Returns:
            List of inappropriate content types found
        """
        inappropriate = []
        script_text = " ".join([s.text for s in script.segments]).lower()
        
        # Basic inappropriate content patterns
        # This is a simplified check - in production, use more sophisticated filtering
        inappropriate_patterns = {
            "violence": ["kill", "murder", "violence", "attack", "harm"],
            "hate_speech": ["hate", "discriminate", "racist", "sexist"],
            "self_harm": ["suicide", "self-harm", "kill myself"],
        }
        
        for category, patterns in inappropriate_patterns.items():
            for pattern in patterns:
                if pattern in script_text:
                    inappropriate.append(category)
                    break
        
        return inappropriate
    
    def _make_decision(self, score: float, issues: List[Dict[str, Any]]) -> GateDecision:
        """
        Make quality gate decision based on score and issues.
        
        Args:
            score: Quality score (0-1)
            issues: List of issues found
            
        Returns:
            Gate decision
        """
        # Check for high-severity issues
        high_severity = any(i.get("severity") == "high" for i in issues)
        
        if high_severity:
            return GateDecision.FAIL
        
        if score >= QualityLevel.EXCELLENT.value:
            return GateDecision.PASS
        elif score >= QualityLevel.GOOD.value:
            return GateDecision.PASS
        elif score >= self.min_quality_score:
            return GateDecision.WARNING
        else:
            return GateDecision.FAIL
    
    def _generate_recommendations(self, issues: List[Dict[str, Any]]) -> List[str]:
        """
        Generate recommendations based on issues found.
        
        Args:
            issues: List of issues found
            
        Returns:
            List of recommendations
        """
        recommendations = []
        
        category_recommendations = {
            "completeness": "Ensure all required fields are filled with meaningful content",
            "alignment": "Align content with project settings and content plan",
            "actionability": "Provide clear, actionable guidance for content creation",
            "timing": "Adjust script length to match target duration",
            "structure": "Improve script structure with clear segments",
            "content": "Enhance content quality with more detail or better phrasing",
            "engagement": "Strengthen hooks and engagement elements",
            "safety": "Remove or modify inappropriate content",
            "consistency": "Ensure audit decisions align with quality scores",
            "quality": "Address high-severity quality issues before proceeding",
        }
        
        seen_categories = set()
        for issue in issues:
            category = issue.get("category")
            if category and category not in seen_categories:
                if category in category_recommendations:
                    recommendations.append(category_recommendations[category])
                seen_categories.add(category)
        
        return recommendations
