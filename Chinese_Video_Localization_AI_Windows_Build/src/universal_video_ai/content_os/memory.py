"""
Content OS Memory System.

Provides persistent memory storage and retrieval for Content OS workflows,
enabling learning from past runs and improving future content generation.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from enum import Enum
import json

from .repository import ContentOSRepository
from .enums import MemoryType
from .schemas import Memory


class MemorySystem:
    """
    Memory system for Content OS workflows.
    
    Stores and retrieves learned information from past runs to improve
    future content generation and decision-making.
    """
    
    def __init__(self, repository: ContentOSRepository):
        """
        Initialize the memory system.
        
        Args:
            repository: Content OS repository instance
        """
        self.repository = repository
    
    def store_learned_pattern(
        self,
        user_id: int,
        project_id: int,
        pattern_type: str,
        pattern_data: Dict[str, Any],
        confidence: float = 0.8,
        source_run_id: Optional[int] = None,
    ) -> Memory:
        """
        Store a learned pattern from a workflow run.
        
        Args:
            user_id: User ID
            project_id: Project ID
            pattern_type: Type of pattern (e.g., "successful_hook", "engaging_pacing")
            pattern_data: Pattern data as JSON-serializable dict
            confidence: Confidence score (0-1)
            source_run_id: Run ID where pattern was learned
            
        Returns:
            Created memory entry
        """
        memory = self.repository.upsert_memory(
            user_id=user_id,
            memory_type=MemoryType.PATTERN,
            key=f"{pattern_type}_{datetime.utcnow().isoformat()}",
            value=json.dumps({
                "pattern_type": pattern_type,
                "pattern_data": pattern_data,
                "confidence": confidence,
                "source_run_id": source_run_id,
            }),
            project_id=project_id,
            expires_at=datetime.utcnow() + timedelta(days=90),
        )
        return memory
    
    def store_user_preference(
        self,
        user_id: int,
        preference_key: str,
        preference_value: Any,
        project_id: Optional[int] = None,
    ) -> Memory:
        """
        Store a user preference.
        
        Args:
            user_id: User ID
            preference_key: Preference identifier (e.g., "preferred_tone", "default_duration")
            preference_value: Preference value
            project_id: Optional project ID for project-specific preferences
            
        Returns:
            Created or updated memory entry
        """
        memory = self.repository.upsert_memory(
            user_id=user_id,
            memory_type=MemoryType.PREFERENCE,
            key=preference_key,
            value=json.dumps(preference_value),
            project_id=project_id,
            expires_at=None,  # Preferences don't expire
        )
        return memory
    
    def store_performance_metric(
        self,
        user_id: int,
        run_id: int,
        metric_name: str,
        metric_value: float,
        project_id: Optional[int] = None,
    ) -> Memory:
        """
        Store a performance metric from a completed run.
        
        Args:
            user_id: User ID
            run_id: Run ID
            metric_name: Metric name (e.g., "engagement_rate", "retention_rate")
            metric_value: Metric value
            project_id: Optional project ID
            
        Returns:
            Created memory entry
        """
        memory = self.repository.upsert_memory(
            user_id=user_id,
            memory_type=MemoryType.METRIC,
            key=f"{metric_name}_run_{run_id}",
            value=json.dumps({
                "run_id": run_id,
                "metric_name": metric_name,
                "metric_value": metric_value,
                "timestamp": datetime.utcnow().isoformat(),
            }),
            project_id=project_id,
            expires_at=datetime.utcnow() + timedelta(days=365),
        )
        return memory
    
    def get_user_preferences(
        self,
        user_id: int,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get all user preferences.
        
        Args:
            user_id: User ID
            project_id: Optional project ID for project-specific preferences
            
        Returns:
            Dictionary of preference key-value pairs
        """
        memories = self.repository.list_memories(
            user_id=user_id,
            memory_type=MemoryType.PREFERENCE,
            project_id=project_id,
            active_only=True,
        )
        
        preferences = {}
        for memory in memories:
            try:
                preferences[memory.key] = json.loads(memory.value)
            except (json.JSONDecodeError, ValueError):
                continue
        
        return preferences
    
    def get_learned_patterns(
        self,
        user_id: int,
        pattern_type: Optional[str] = None,
        project_id: Optional[int] = None,
        min_confidence: float = 0.5,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get learned patterns matching criteria.
        
        Args:
            user_id: User ID
            pattern_type: Optional pattern type filter
            project_id: Optional project ID filter
            min_confidence: Minimum confidence score
            limit: Maximum number of patterns to return
            
        Returns:
            List of pattern dictionaries
        """
        memories = self.repository.list_memories(
            user_id=user_id,
            memory_type=MemoryType.PATTERN,
            project_id=project_id,
            active_only=True,
        )
        
        patterns = []
        for memory in memories:
            try:
                data = json.loads(memory.value)
                confidence = data.get("confidence", 0)
                
                if confidence >= min_confidence:
                    if pattern_type is None or data.get("pattern_type") == pattern_type:
                        patterns.append(data)
                        
            except (json.JSONDecodeError, ValueError):
                continue
        
        # Sort by confidence and limit
        patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)
        return patterns[:limit]
    
    def get_performance_metrics(
        self,
        user_id: int,
        metric_name: Optional[str] = None,
        project_id: Optional[int] = None,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get performance metrics from recent runs.
        
        Args:
            user_id: User ID
            metric_name: Optional metric name filter
            project_id: Optional project ID filter
            days: Number of days to look back
            
        Returns:
            List of metric dictionaries
        """
        memories = self.repository.list_memories(
            user_id=user_id,
            memory_type=MemoryType.METRIC,
            project_id=project_id,
            active_only=True,
        )
        
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        metrics = []
        
        for memory in memories:
            try:
                data = json.loads(memory.value)
                timestamp = datetime.fromisoformat(data.get("timestamp", ""))
                
                if timestamp >= cutoff_date:
                    if metric_name is None or data.get("metric_name") == metric_name:
                        metrics.append(data)
                        
            except (json.JSONDecodeError, ValueError):
                continue
        
        # Sort by timestamp (most recent first)
        metrics.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
        return metrics
    
    def calculate_average_metric(
        self,
        user_id: int,
        metric_name: str,
        project_id: Optional[int] = None,
        days: int = 30,
    ) -> Optional[float]:
        """
        Calculate average value for a metric over time.
        
        Args:
            user_id: User ID
            metric_name: Metric name
            project_id: Optional project ID filter
            days: Number of days to look back
            
        Returns:
            Average metric value, or None if no data available
        """
        metrics = self.get_performance_metrics(
            user_id=user_id,
            metric_name=metric_name,
            project_id=project_id,
            days=days,
        )
        
        if not metrics:
            return None
        
        values = [m.get("metric_value", 0) for m in metrics]
        return sum(values) / len(values)
    
    def get_successful_hooks(
        self,
        user_id: int,
        project_id: Optional[int] = None,
        limit: int = 5,
    ) -> List[str]:
        """
        Get historically successful hook patterns.
        
        Args:
            user_id: User ID
            project_id: Optional project ID filter
            limit: Maximum number of hooks to return
            
        Returns:
            List of hook text patterns
        """
        patterns = self.get_learned_patterns(
            user_id=user_id,
            pattern_type="successful_hook",
            project_id=project_id,
            min_confidence=0.7,
            limit=limit * 2,  # Get more to filter
        )
        
        hooks = []
        for pattern in patterns:
            hook_text = pattern.get("pattern_data", {}).get("hook_text")
            if hook_text:
                hooks.append(hook_text)
        
        return hooks[:limit]
    
    def get_preferred_content_settings(
        self,
        user_id: int,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get user's preferred content settings based on history.
        
        Args:
            user_id: User ID
            project_id: Optional project ID filter
            
        Returns:
            Dictionary of preferred settings
        """
        preferences = self.get_user_preferences(user_id, project_id)
        
        # Default settings
        default_settings = {
            "preferred_tone": "casual",
            "preferred_pacing": "moderate",
            "default_duration": 45,
            "preferred_style": "entertaining",
        }
        
        # Override with user preferences
        default_settings.update(preferences)
        
        return default_settings
    
    def cleanup_expired_memories(
        self,
        user_id: int,
    ) -> int:
        """
        Clean up expired memory entries.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of memories cleaned up
        """
        # This would be implemented in the repository
        # For now, return 0 as placeholder
        return 0
