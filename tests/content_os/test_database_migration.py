"""
Test Content OS database migration in web/store.py.

Verifies that Content OS tables are created correctly and that
the migration is idempotent (safe to run multiple times).
"""
import pytest
import sqlite3
import tempfile
import json
from pathlib import Path

from universal_video_ai.web.store import Store
from universal_video_ai.content_os.repository import ContentOSRepository


class TestContentOSDatabaseMigration:
    """Test Content OS database table creation."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        # Cleanup - Windows needs connections closed first
        import gc
        gc.collect()
        try:
            if db_path.exists():
                db_path.unlink(missing_ok=True)
        except PermissionError:
            # File may still be locked on Windows, skip cleanup
            pass
    
    @pytest.fixture
    def store(self, temp_db_path):
        """Create a Store instance with temporary database."""
        return Store(db_path=temp_db_path)
    
    def test_content_os_tables_created(self, store):
        """Verify all Content OS tables are created."""
        with store._connect() as conn:
            cursor = conn.cursor()
            
            # List all tables
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row["name"] for row in cursor.fetchall()]
            
            # Verify Content OS tables exist
            content_os_tables = [
                "content_os_projects",
                "content_os_runs",
                "content_os_steps",
                "content_os_artifacts",
                "content_os_sources",
                "content_os_reviews",
                "content_os_approvals",
                "content_os_memories",
            ]
            
            for table in content_os_tables:
                assert table in tables, f"Table {table} not found in database"
    
    def test_content_os_projects_schema(self, store):
        """Verify content_os_projects table has correct columns."""
        with store._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(content_os_projects)")
            columns = {row["name"]: row for row in cursor.fetchall()}
            
            expected_columns = [
                "id", "user_id", "channel_id", "channel_name", "mode", "topic",
                "objective", "target_platform", "target_duration_seconds",
                "target_language", "content_style", "visual_style",
                "voice_id", "subtitle_style_id", "background_music_enabled",
                "user_instructions", "settings_json", "status",
                "created_at", "updated_at"
            ]
            
            for col in expected_columns:
                assert col in columns, f"Column {col} not found in content_os_projects"
    
    def test_content_os_runs_schema(self, store):
        """Verify content_os_runs table has correct columns."""
        with store._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(content_os_runs)")
            columns = {row["name"]: row for row in cursor.fetchall()}
            
            expected_columns = [
                "id", "project_id", "user_id", "workflow_version",
                "status", "current_stage", "progress_percent",
                "revision_count", "warning_json", "error_json",
                "created_at", "started_at", "completed_at", "updated_at"
            ]
            
            for col in expected_columns:
                assert col in columns, f"Column {col} not found in content_os_runs"
    
    def test_content_os_artifacts_schema(self, store):
        """Verify content_os_artifacts table has correct columns."""
        with store._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(content_os_artifacts)")
            columns = {row["name"]: row for row in cursor.fetchall()}
            
            expected_columns = [
                "id", "run_id", "user_id", "artifact_type",
                "version", "schema_version", "path", "checksum",
                "metadata_json", "created_by_agent", "created_at"
            ]
            
            for col in expected_columns:
                assert col in columns, f"Column {col} not found in content_os_artifacts"
    
    def test_content_os_indexes_created(self, store):
        """Verify Content OS indexes are created."""
        with store._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_content_os_%' ORDER BY name"
            )
            indexes = [row["name"] for row in cursor.fetchall()]
            
            # Verify key indexes exist
            expected_indexes = [
                "idx_content_os_projects_user_id",
                "idx_content_os_runs_user_id",
                "idx_content_os_runs_status",
                "idx_content_os_artifacts_run_id",
                "idx_content_os_sources_run_id",
                "idx_content_os_memories_user_id",
            ]
            
            for idx in expected_indexes:
                assert idx in indexes, f"Index {idx} not found"
    
    def test_migration_idempotent(self, temp_db_path):
        """Verify migration can be run multiple times safely."""
        # First initialization
        Store(db_path=temp_db_path)
        
        # Second initialization (should not fail)
        Store(db_path=temp_db_path)
        
        # Third initialization (should still not fail)
        Store(db_path=temp_db_path)
        
        # Verify tables still exist and are intact
        with sqlite3.connect(str(temp_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row["name"] for row in cursor.fetchall()]
            
            assert "content_os_projects" in tables
            assert "content_os_runs" in tables
            assert "content_os_artifacts" in tables
    
    def test_foreign_keys_exist(self, store):
        """Verify foreign key constraints are defined."""
        with store._connect() as conn:
            cursor = conn.cursor()
            
            # Check content_os_runs -> content_os_projects foreign key
            cursor.execute("PRAGMA foreign_key_list(content_os_runs)")
            fk_runs = cursor.fetchall()
            assert len(fk_runs) > 0, "No foreign keys found in content_os_runs"
            
            # Check content_os_artifacts -> content_os_runs foreign key
            cursor.execute("PRAGMA foreign_key_list(content_os_artifacts)")
            fk_artifacts = cursor.fetchall()
            assert len(fk_artifacts) > 0, "No foreign keys found in content_os_artifacts"

    def test_legacy_project_schema_remains_writable(self, temp_db_path):
        """Upgraded databases with legacy NOT NULL columns accept new projects."""
        with sqlite3.connect(str(temp_db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE content_os_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_name TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    target_platforms_json TEXT NOT NULL,
                    source_platforms_json TEXT NOT NULL,
                    target_market TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    target_duration_seconds INTEGER NOT NULL,
                    content_format TEXT NOT NULL,
                    settings_json TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO content_os_projects
                (user_id, channel_name, topic, target_platforms_json,
                 source_platforms_json, target_market, target_language,
                 target_duration_seconds, content_format, created_at, updated_at)
                VALUES (1, 'Legacy', 'Existing', '["tiktok"]', '[]',
                        'Vietnam', 'vi', 45, 'trend_decode', 1, 1)
                """
            )

        Store(db_path=temp_db_path)
        repo = ContentOSRepository(temp_db_path)

        migrated = repo.get_project(1, user_id=1)
        assert migrated.target_platform == "tiktok"

        created = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="New",
            mode="ai_video",
            topic="New topic",
            objective="",
            target_platform="instagram_reels",
            target_duration_seconds=30,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        assert created.target_platform == "instagram_reels"

        with sqlite3.connect(str(temp_db_path)) as conn:
            legacy_platforms = conn.execute(
                "SELECT target_platforms_json FROM content_os_projects WHERE id = ?",
                (created.id,),
            ).fetchone()[0]
        assert json.loads(legacy_platforms) == ["instagram_reels"]
