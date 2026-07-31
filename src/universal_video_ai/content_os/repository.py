"""
Content OS repository layer.

Database operations for Content OS entities.

This repository uses the web store database (web/store.py) which already
contains the Content OS tables via the SCHEMA migration.
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from .models import (
    ContentOSChannel,
    ContentOSProject,
    ContentOSRun,
    ContentOSStep,
    ContentOSArtifact,
    ContentOSSource,
    ContentOSReview,
    ContentOSApproval,
    ContentOSMemory,
)
from .enums import WorkflowStage, RunStatus, StepStatus, ArtifactType, ApprovalType, MemoryType

logger = logging.getLogger(__name__)


class ContentOSRepository:
    """
    Repository for Content OS database operations.
    
    Uses the existing web store database connection pattern.
    The tables are created by the web store's SCHEMA migration.
    """
    
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        # Schema is managed by web/store.py, no need to init here
    
    def _connect(self):
        """Get database connection using web store pattern."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    # ==================== Channels ====================
    
    def create_channel(
        self,
        user_id: int,
        channel_name: str,
        platforms: List[str],
        niche: str,
        target_audience: str,
        target_market: str,
        default_language: str,
        tone: str,
        visual_identity: Dict[str, Any],
        default_voice: str,
        subtitle_profile: Dict[str, Any],
        content_rules: List[str],
        forbidden_topics: List[str],
        preferred_formats: List[str],
        publishing_notes: str,
    ) -> ContentOSChannel:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_channels 
                (user_id, channel_name, platforms_json, niche, target_audience, target_market,
                 default_language, tone, visual_identity_json, default_voice, subtitle_profile_json,
                 content_rules_json, forbidden_topics_json, preferred_formats_json, publishing_notes,
                 active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, channel_name, json.dumps(platforms), niche, target_audience, target_market,
                    default_language, tone, json.dumps(visual_identity), default_voice, json.dumps(subtitle_profile),
                    json.dumps(content_rules), json.dumps(forbidden_topics), json.dumps(preferred_formats),
                    publishing_notes, True, now, now,
                ),
            )
            channel_id = cur.lastrowid
            conn.commit()
        
        return self.get_channel(channel_id, user_id)
    
    def get_channel(self, channel_id: int, user_id: int) -> Optional[ContentOSChannel]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM content_os_channels WHERE id = ? AND user_id = ?",
                (channel_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSChannel(**dict(row))
    
    def list_channels(self, user_id: int, active_only: bool = True) -> List[ContentOSChannel]:
        with self._connect() as conn:
            if active_only:
                cur = conn.execute(
                    "SELECT * FROM content_os_channels WHERE user_id = ? AND active = ? ORDER BY created_at DESC",
                    (user_id, True),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM content_os_channels WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )
            return [ContentOSChannel(**dict(row)) for row in cur.fetchall()]
    
    def update_channel(
        self,
        channel_id: int,
        user_id: int,
        **updates
    ) -> Optional[ContentOSChannel]:
        """Update channel fields. Pass JSON fields as Python objects."""
        allowed_fields = {
            'channel_name', 'platforms', 'niche', 'target_audience', 'target_market',
            'default_language', 'tone', 'visual_identity', 'default_voice',
            'subtitle_profile', 'content_rules', 'forbidden_topics', 'preferred_formats',
            'publishing_notes', 'active'
        }
        
        # Filter to allowed fields
        updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not updates:
            return self.get_channel(channel_id, user_id)
        
        # Convert list/dict fields to JSON
        json_fields = {
            'platforms', 'visual_identity', 'subtitle_profile', 'content_rules',
            'forbidden_topics', 'preferred_formats'
        }
        
        set_clauses = []
        values = []
        for field, value in updates.items():
            if field in json_fields:
                set_clauses.append(f"{field}_json = ?")
                values.append(json.dumps(value))
            else:
                set_clauses.append(f"{field} = ?")
                values.append(value)
        
        set_clauses.append("updated_at = ?")
        values.append(time.time())
        values.append(channel_id)
        values.append(user_id)
        
        with self._connect() as conn:
            conn.execute(
                f"UPDATE content_os_channels SET {', '.join(set_clauses)} WHERE id = ? AND user_id = ?",
                values,
            )
            conn.commit()
        
        return self.get_channel(channel_id, user_id)
    
    def delete_channel(self, channel_id: int, user_id: int) -> bool:
        """Soft delete channel by setting active=False."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE content_os_channels SET active = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (False, time.time(), channel_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    
    # ==================== Projects ====================
    
    def create_project(
        self,
        user_id: int,
        channel_id: Optional[int],
        channel_name: str,
        mode: str,
        topic: str,
        objective: str,
        target_platform: str,
        target_duration_seconds: int,
        target_language: str,
        content_style: str,
        visual_style: str,
        voice_id: str,
        subtitle_style_id: str,
        background_music_enabled: bool,
        user_instructions: str,
    ) -> ContentOSProject:
        now = time.time()
        settings = {
            "content_style": content_style,
            "visual_style": visual_style,
            "voice_id": voice_id,
            "subtitle_style_id": subtitle_style_id,
        }
        
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_projects 
                (user_id, channel_id, channel_name, mode, topic, objective, target_platform,
                 target_duration_seconds, target_language, content_style, visual_style,
                 voice_id, subtitle_style_id, background_music_enabled, user_instructions,
                 settings_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, channel_id, channel_name, mode, topic, objective, target_platform,
                    target_duration_seconds, target_language, content_style, visual_style,
                    voice_id, subtitle_style_id, int(background_music_enabled), user_instructions,
                    json.dumps(settings), "active", now, now,
                ),
            )
            project_id = cur.lastrowid
            conn.commit()
        
        return self.get_project(project_id, user_id)
    
    def get_project(self, project_id: int, user_id: int) -> Optional[ContentOSProject]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT id, user_id, channel_name, mode, topic, objective, 
                       target_platform, target_duration_seconds, target_language, 
                       content_style, visual_style, voice_id, subtitle_style_id, 
                       background_music_enabled, user_instructions, settings_json, 
                       status, created_at, updated_at
                FROM content_os_projects WHERE id = ? AND user_id = ?
                """,
                (project_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            # Handle missing channel_id in older schema
            row_dict = dict(row)
            if 'channel_id' not in row_dict:
                row_dict['channel_id'] = None
            return ContentOSProject(**row_dict)
    
    def list_projects(self, user_id: int, limit: int = 100) -> List[ContentOSProject]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT id, user_id, channel_name, mode, topic, objective, 
                       target_platform, target_duration_seconds, target_language, 
                       content_style, visual_style, voice_id, subtitle_style_id, 
                       background_music_enabled, user_instructions, settings_json, 
                       status, created_at, updated_at
                FROM content_os_projects WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, limit),
            )
            # Handle missing channel_id in older schema
            projects = []
            for row in cur.fetchall():
                row_dict = dict(row)
                if 'channel_id' not in row_dict:
                    row_dict['channel_id'] = None
                projects.append(ContentOSProject(**row_dict))
            return projects
    
    def update_project(self, project_id: int, user_id: int, **fields: Any) -> bool:
        allowed = {
            "channel_id", "channel_name", "mode", "topic", "objective", "target_platform",
            "target_duration_seconds", "target_language", "content_style", "visual_style",
            "voice_id", "subtitle_style_id", "background_music_enabled", "user_instructions",
            "settings_json", "status",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        
        # Convert boolean to integer for background_music_enabled
        if "background_music_enabled" in updates:
            updates["background_music_enabled"] = int(updates["background_music_enabled"])
        
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [time.time(), project_id, user_id]
        
        with self._connect() as conn:
            conn.execute(
                f"UPDATE content_os_projects SET {set_clause}, updated_at = ? WHERE id = ? AND user_id = ?",
                values,
            )
            conn.commit()
            return True
    
    def delete_project(self, project_id: int, user_id: int) -> bool:
        """Delete a project and all its associated data."""
        with self._connect() as conn:
            # Delete runs first (cascade)
            conn.execute(
                "DELETE FROM content_os_runs WHERE project_id = ? AND user_id = ?",
                (project_id, user_id)
            )
            # Delete project
            conn.execute(
                "DELETE FROM content_os_projects WHERE id = ? AND user_id = ?",
                (project_id, user_id)
            )
            conn.commit()
            return True
    
    # ==================== Runs ====================
    
    def create_run(self, project_id: int, user_id: int) -> ContentOSRun:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_runs 
                (project_id, user_id, workflow_version, status, current_stage, 
                 progress_percent, revision_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, user_id, "1.0", "created", "created", 0, 0, now, now),
            )
            run_id = cur.lastrowid
            conn.commit()
        
        return self.get_run(run_id, user_id)
    
    def get_run(self, run_id: int, user_id: int) -> Optional[ContentOSRun]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM content_os_runs WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSRun(**dict(row))
    
    def update_run(self, run_id: int, user_id: int, **fields: Any) -> bool:
        allowed = {
            "status", "current_stage", "progress_percent", "revision_count",
            "warning_json", "error_json", "started_at", "completed_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [time.time(), run_id, user_id]
        
        with self._connect() as conn:
            conn.execute(
                f"UPDATE content_os_runs SET {set_clause}, updated_at = ? WHERE id = ? AND user_id = ?",
                values,
            )
            conn.commit()
            return True
    
    def list_runs(self, user_id: int, project_id: Optional[int] = None, limit: int = 100) -> List[ContentOSRun]:
        with self._connect() as conn:
            if project_id:
                cur = conn.execute(
                    "SELECT * FROM content_os_runs WHERE user_id = ? AND project_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (user_id, project_id, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM content_os_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                )
            return [ContentOSRun(**dict(row)) for row in cur.fetchall()]
    
    # ==================== Steps ====================
    
    def create_step(
        self,
        run_id: int,
        stage: str,
        agent_name: str,
        input_artifact_ids: List[int] = None,
    ) -> ContentOSStep:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_steps 
                (run_id, stage, agent_name, status, input_artifact_ids_json, 
                 output_artifact_ids_json, attempt, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, stage, agent_name, "pending",
                    json.dumps(input_artifact_ids or []),
                    json.dumps([]),
                    1, now,
                ),
            )
            step_id = cur.lastrowid
            conn.commit()
        
        return self.get_step(step_id)
    
    def get_step(self, step_id: int) -> Optional[ContentOSStep]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM content_os_steps WHERE id = ?", (step_id,))
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSStep(**dict(row))
    
    def update_step(self, step_id: int, **fields: Any) -> bool:
        allowed = {"status", "output_artifact_ids_json", "started_at", "completed_at", "error_json"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [step_id]
        
        with self._connect() as conn:
            conn.execute(
                f"UPDATE content_os_steps SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            return True
    
    def list_steps(self, run_id: int) -> List[ContentOSStep]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM content_os_steps WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
            return [ContentOSStep(**dict(row)) for row in cur.fetchall()]
    
    # ==================== Artifacts ====================
    
    def create_artifact(
        self,
        run_id: int,
        user_id: int,
        artifact_type: str,
        version: int,
        schema_version: str,
        path: str,
        checksum: str,
        metadata: Dict[str, Any],
        created_by_agent: str,
    ) -> ContentOSArtifact:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_artifacts 
                (run_id, user_id, artifact_type, version, schema_version, path, 
                 checksum, metadata_json, created_by_agent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, user_id, artifact_type, version, schema_version,
                    path, checksum, json.dumps(metadata), created_by_agent, now,
                ),
            )
            artifact_id = cur.lastrowid
            conn.commit()
        
        return self.get_artifact(artifact_id)
    
    def get_artifact(self, artifact_id: int) -> Optional[ContentOSArtifact]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM content_os_artifacts WHERE id = ?", (artifact_id,))
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSArtifact(**dict(row))
    
    def list_artifacts(self, run_id: int) -> List[ContentOSArtifact]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM content_os_artifacts WHERE run_id = ? ORDER BY version",
                (run_id,),
            )
            return [ContentOSArtifact(**dict(row)) for row in cur.fetchall()]
    
    # ==================== Sources ====================
    
    def create_source(
        self,
        run_id: int,
        user_id: int,
        platform: str,
        provider: str,
        source_url: str,
        canonical_url: str,
        title: str,
        author: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        metrics: Dict[str, Any] = None,
        trend_score: float = 0.0,
        raw_metadata: Dict[str, Any] = None,
    ) -> ContentOSSource:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_sources 
                (run_id, user_id, platform, provider, source_url, canonical_url, title,
                 author, thumbnail_url, metrics_json, trend_score, risk_json, raw_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, user_id, platform, provider, source_url, canonical_url, title,
                    author, thumbnail_url,
                    json.dumps(metrics or {}),
                    trend_score,
                    json.dumps({"reuse_risk": "medium", "copyright_risk": "medium"}),
                    json.dumps(raw_metadata or {}),
                    now, now,
                ),
            )
            source_id = cur.lastrowid
            conn.commit()
        
        return self.get_source(source_id, user_id)
    
    def get_source(self, source_id: int, user_id: int) -> Optional[ContentOSSource]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM content_os_sources WHERE id = ? AND user_id = ?",
                (source_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSSource(**dict(row))
    
    def update_source(self, source_id: int, user_id: int, **fields: Any) -> bool:
        allowed = {
            "selected", "download_status", "local_path", "risk_json", "metrics_json",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [time.time(), source_id, user_id]
        
        with self._connect() as conn:
            conn.execute(
                f"UPDATE content_os_sources SET {set_clause}, updated_at = ? WHERE id = ? AND user_id = ?",
                values,
            )
            conn.commit()
            return True
    
    def list_sources(self, run_id: int, user_id: int) -> List[ContentOSSource]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM content_os_sources WHERE run_id = ? AND user_id = ? ORDER BY trend_score DESC",
                (run_id, user_id),
            )
            return [ContentOSSource(**dict(row)) for row in cur.fetchall()]
    
    # ==================== Approvals ====================
    
    def create_approval(
        self,
        run_id: int,
        user_id: int,
        approval_type: str,
        decision: str,
        note: str = "",
    ) -> ContentOSApproval:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_os_approvals 
                (run_id, user_id, approval_type, decision, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, user_id, approval_type, decision, note, now),
            )
            approval_id = cur.lastrowid
            conn.commit()
        
        return self.get_approval(approval_id)
    
    def get_approval(self, approval_id: int) -> Optional[ContentOSApproval]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM content_os_approvals WHERE id = ?", (approval_id,))
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSApproval(**dict(row))
    
    def get_latest_approval(
        self,
        run_id: int,
        approval_type: str,
    ) -> Optional[ContentOSApproval]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM content_os_approvals 
                WHERE run_id = ? AND approval_type = ? 
                ORDER BY created_at DESC LIMIT 1
                """,
                (run_id, approval_type),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSApproval(**dict(row))
    
    # ==================== Memories ====================
    
    def upsert_memory(
        self,
        user_id: int,
        channel_key: str,
        memory_type: str,
        memory_key: str,
        value: Any,
        confidence: float = 0.5,
        source_run_id: Optional[int] = None,
        active: bool = True,
    ) -> ContentOSMemory:
        now = time.time()
        with self._connect() as conn:
            # Try update first
            cur = conn.execute(
                """
                UPDATE content_os_memories 
                SET value_json = ?, confidence = ?, source_run_id = ?, active = ?, updated_at = ?
                WHERE user_id = ? AND channel_key = ? AND memory_type = ? AND memory_key = ?
                """,
                (
                    json.dumps(value), confidence, source_run_id, int(active), now,
                    user_id, channel_key, memory_type, memory_key,
                ),
            )
            if cur.rowcount > 0:
                conn.commit()
                return self.get_memory(user_id, channel_key, memory_type, memory_key)
            
            # Insert new
            cur = conn.execute(
                """
                INSERT INTO content_os_memories 
                (user_id, channel_key, memory_type, memory_key, value_json, 
                 confidence, source_run_id, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, channel_key, memory_type, memory_key,
                    json.dumps(value), confidence, source_run_id, int(active), now, now,
                ),
            )
            memory_id = cur.lastrowid
            conn.commit()
        
        return self.get_memory(user_id, channel_key, memory_type, memory_key)
    
    def get_memory(
        self,
        user_id: int,
        channel_key: str,
        memory_type: str,
        memory_key: str,
    ) -> Optional[ContentOSMemory]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM content_os_memories 
                WHERE user_id = ? AND channel_key = ? AND memory_type = ? AND memory_key = ?
                """,
                (user_id, channel_key, memory_type, memory_key),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ContentOSMemory(**dict(row))
    
    def list_memories(
        self,
        user_id: int,
        channel_key: Optional[str] = None,
        memory_type: Optional[str] = None,
        active_only: bool = True,
    ) -> List[ContentOSMemory]:
        with self._connect() as conn:
            query = "SELECT * FROM content_os_memories WHERE user_id = ?"
            params = [user_id]
            
            if channel_key:
                query += " AND channel_key = ?"
                params.append(channel_key)
            if memory_type:
                query += " AND memory_type = ?"
                params.append(memory_type)
            if active_only:
                query += " AND active = 1"
            
            query += " ORDER BY confidence DESC, updated_at DESC"
            
            cur = conn.execute(query, params)
            return [ContentOSMemory(**dict(row)) for row in cur.fetchall()]
