"""
Tests for context compiler.
"""
import pytest
from universal_video_ai.content_os.context_compiler import ContextCompiler, CompiledContext
from universal_video_ai.content_os.enums import WorkflowStage
from universal_video_ai.content_os.repository import ContentOSRepository


@pytest.fixture
def temp_db(tmp_path):
    """Temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def repo(temp_db):
    """Repository instance with initialized schema."""
    from universal_video_ai.web.store import Store
    Store(db_path=temp_db)
    return ContentOSRepository(temp_db)


@pytest.fixture
def compiler(repo):
    """Context compiler instance."""
    return ContextCompiler(repo)


class TestContextCompiler:
    """Test context compiler."""
    
    def test_compile_basic_context(self, compiler, repo):
        """Test compiling basic context without channel."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        
        run = repo.create_run(project.id, user_id=1)
        
        context = compiler.compile(
            user_id=1,
            project_id=project.id,
            run_id=run.id,
            stage=WorkflowStage.SCRIPT_WRITING,
        )
        
        assert isinstance(context, CompiledContext)
        assert context.user_id == 1
        assert context.project_id == project.id
        assert context.run_id == run.id
        assert context.channel_id is None
        assert context.channel_name == "Test Channel"
        assert context.topic == "AI gadgets"
        assert context.target_platform == "youtube_shorts"
    
    def test_compile_with_channel(self, compiler, repo):
        """Test compiling context with channel."""
        channel = repo.create_channel(
            user_id=1,
            channel_name="Tech Channel",
            platforms=["youtube_shorts", "tiktok"],
            niche="technology",
            target_audience="tech enthusiasts",
            target_market="Vietnam",
            default_language="vi",
            tone="professional",
            visual_identity={"primary_color": "#FF0000"},
            default_voice="",
            subtitle_profile={},
            content_rules=["No clickbait", "Accurate information"],
            forbidden_topics=["politics"],
            preferred_formats=["tutorial", "review"],
            publishing_notes="",
        )
        
        project = repo.create_project(
            user_id=1,
            channel_id=channel.id,
            channel_name="Tech Channel",
            mode="ai_video",
            topic="AI gadgets",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        
        run = repo.create_run(project.id, user_id=1)
        
        context = compiler.compile(
            user_id=1,
            project_id=project.id,
            run_id=run.id,
            stage=WorkflowStage.SCRIPT_WRITING,
        )
        
        assert context.channel_id == channel.id
        assert context.niche == "technology"
        assert context.target_audience == "tech enthusiasts"
        assert context.tone == "professional"
        assert "No clickbait" in context.content_rules
        assert "politics" in context.forbidden_topics
    
    def test_compile_with_memory(self, compiler, repo):
        """Test compiling context with relevant memories."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        
        run = repo.create_run(project.id, user_id=1)
        
        # Add some memories that match SCRIPT_WRITING stage types
        repo.upsert_memory(
            user_id=1,
            channel_key="Test Channel",
            memory_type="tone_patterns",
            memory_key="preferred_tone",
            value="casual",
        )
        
        repo.upsert_memory(
            user_id=1,
            channel_key="Test Channel",
            memory_type="language_style",
            memory_key="style",
            value="conversational",
        )
        
        context = compiler.compile(
            user_id=1,
            project_id=project.id,
            run_id=run.id,
            stage=WorkflowStage.SCRIPT_WRITING,
        )
        
        assert len(context.relevant_memories) > 0
    
    def test_format_for_agent(self, compiler, repo):
        """Test formatting context for agent consumption."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="Make it engaging",
        )
        
        run = repo.create_run(project.id, user_id=1)
        
        context = compiler.compile(
            user_id=1,
            project_id=project.id,
            run_id=run.id,
            stage=WorkflowStage.SCRIPT_WRITING,
        )
        
        formatted = compiler.format_for_agent(context)
        
        assert "Channel: Test Channel" in formatted
        assert "Project: AI gadgets" in formatted
        assert "Make it engaging" in formatted
        assert "youtube_shorts" in formatted
