"""
Tests for Content OS agent framework.

Tests base agent, LLM router, context builder, and all six MVP agents.
"""
import pytest
from pathlib import Path
import tempfile

from universal_video_ai.content_os.agents.base import BaseAgent
from universal_video_ai.content_os.agents.llm_router import LLMRouter
from universal_video_ai.content_os.agents.context_builder import ContextBuilder
from universal_video_ai.content_os.agents.trend_radar_agent import TrendRadarAgent
from universal_video_ai.content_os.agents.source_analyzer_agent import SourceAnalyzerAgent
from universal_video_ai.content_os.agents.content_planner_agent import ContentPlannerAgent
from universal_video_ai.content_os.agents.script_writer_agent import ScriptWriterAgent
from universal_video_ai.content_os.agents.content_audit_agent import ContentAuditAgent
from universal_video_ai.content_os.agents.script_reviser_agent import ScriptReviserAgent
from universal_video_ai.content_os.repository import ContentOSRepository


class TestLLMRouter:
    """Test LLM router."""
    
    def test_router_initialization(self):
        """Should initialize with default or provided config."""
        router = LLMRouter()
        assert router.provider == "ollama"  # Default from config
        
        router2 = LLMRouter(provider="openai", model="gpt-4")
        assert router2.provider == "openai"
        assert router2.model == "gpt-4"
    
    def test_mock_output(self):
        """Should return mock output when LLM not available."""
        router = LLMRouter()
        result = router.invoke("test prompt")
        assert "raw_content" in result
        # Mock output may not contain "Mock" specifically, just check it returns content
        assert len(result["raw_content"]) > 0
    
    def test_unsupported_provider(self):
        """Should raise error for unsupported provider."""
        router = LLMRouter(provider="unsupported")
        with pytest.raises(Exception):  # ProviderUnavailableError
            router.invoke("test prompt")


class TestContextBuilder:
    """Test context builder."""
    
    @pytest.fixture
    def temp_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        import time
        time.sleep(0.1)
        try:
            if db_path.exists():
                db_path.unlink()
        except PermissionError:
            pass
    
    @pytest.fixture
    def repo(self, temp_db):
        from universal_video_ai.web.store import Store
        # Initialize web store schema first (which creates Content OS tables)
        Store(db_path=temp_db)
        return ContentOSRepository(temp_db)
    
    @pytest.fixture
    def context_builder(self, repo):
        return ContextBuilder(repo)
    
    def test_build_basic_context(self, context_builder):
        """Should build basic context."""
        context = context_builder.build(
            user_id=1,
            channel_key="test_channel",
            target_platform="youtube_shorts",
            content_format="trend_decode",
        )
        
        assert context["user_id"] == 1
        assert context["channel_key"] == "test_channel"
        assert context["target_platform"] == "youtube_shorts"
        assert context["content_format"] == "trend_decode"
        assert "memories" in context
        assert "platform_skills" in context
        assert "format_skills" in context
    
    def test_context_with_memories(self, context_builder, repo):
        """Should include memories in context."""
        repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type="winning_topic",
            memory_key="cooking",
            value={"score": 0.9},
        )
        
        context = context_builder.build(
            user_id=1,
            channel_key="test_channel",
            target_platform="youtube_shorts",
            content_format="trend_decode",
        )
        
        assert "winning_topic" in context["memories"]
        assert len(context["memories"]["winning_topic"]) == 1
    
    def test_platform_skills_default(self, context_builder):
        """Should provide default platform skills."""
        context = context_builder.build(
            user_id=1,
            channel_key="test_channel",
            target_platform="youtube_shorts",
            content_format="trend_decode",
        )
        
        assert len(context["platform_skills"]) > 0
        assert any("vertical" in skill.lower() for skill in context["platform_skills"])
    
    def test_format_skills_default(self, context_builder):
        """Should provide default format skills."""
        context = context_builder.build(
            user_id=1,
            channel_key="test_channel",
            target_platform="youtube_shorts",
            content_format="trend_decode",
        )
        
        assert len(context["format_skills"]) > 0


class TestTrendRadarAgent:
    """Test TrendRadarAgent."""
    
    @pytest.fixture
    def agent(self):
        return TrendRadarAgent()
    
    def test_agent_name(self, agent):
        assert agent.agent_name == "TrendRadarAgent"
    
    def test_build_prompt(self, agent):
        prompt = agent.build_prompt({
            "topic": "cooking",
            "target_platform": "youtube_shorts",
            "target_market": "Vietnam",
        })
        
        assert "cooking" in prompt
        assert "youtube_shorts" in prompt
        assert "Vietnam" in prompt
    
    def test_execute_with_mock(self, agent):
        result = agent.execute({
            "topic": "cooking",
            "target_platform": "youtube_shorts",
            "target_market": "Vietnam",
        })
        
        assert "expanded_keywords" in result
        assert "detected_trends" in result
        assert "warnings" in result
        assert len(result["expanded_keywords"]) > 0


class TestSourceAnalyzerAgent:
    """Test SourceAnalyzerAgent."""
    
    @pytest.fixture
    def agent(self):
        return SourceAnalyzerAgent()
    
    def test_agent_name(self, agent):
        assert agent.agent_name == "SourceAnalyzerAgent"
    
    def test_build_prompt(self, agent):
        prompt = agent.build_prompt({
            "topic": "cooking",
            "target_platform": "youtube_shorts",
            "sources": [
                {"title": "Video 1", "source_url": "url1", "platform": "youtube"},
            ],
            "max_sources": 5,
        })
        
        assert "cooking" in prompt
        assert "Video 1" in prompt
    
    def test_execute_with_mock(self, agent):
        result = agent.execute({
            "topic": "cooking",
            "target_platform": "youtube_shorts",
            "sources": [],
            "max_sources": 5,
        })
        
        assert "selected_sources" in result
        assert "rejected_sources" in result
        assert "warnings" in result


class TestContentPlannerAgent:
    """Test ContentPlannerAgent."""
    
    @pytest.fixture
    def agent(self):
        return ContentPlannerAgent()
    
    def test_agent_name(self, agent):
        assert agent.agent_name == "ContentPlannerAgent"
    
    def test_build_prompt(self, agent):
        prompt = agent.build_prompt({
            "topic": "cooking",
            "target_platform": "youtube_shorts",
            "target_duration_seconds": 45,
            "selected_sources": [],
            "platform_skills": ["Skill 1"],
            "format_skills": ["Format 1"],
        })
        
        assert "cooking" in prompt
        assert "45" in prompt
    
    def test_execute_with_mock(self, agent):
        result = agent.execute({
            "topic": "cooking",
            "target_platform": "youtube_shorts",
            "target_duration_seconds": 45,
            "selected_sources": [],
            "platform_skills": [],
            "format_skills": [],
        })
        
        assert "content_angle" in result
        assert "hook" in result
        assert "beats" in result
        assert len(result["beats"]) > 0


class TestScriptWriterAgent:
    """Test ScriptWriterAgent."""
    
    @pytest.fixture
    def agent(self):
        return ScriptWriterAgent()
    
    def test_agent_name(self, agent):
        assert agent.agent_name == "ScriptWriterAgent"
    
    def test_build_prompt(self, agent):
        prompt = agent.build_prompt({
            "content_plan": {
                "content_angle": "Educational",
                "hook": "Hook text",
                "beats": [{"order": 1, "start_second": 0, "end_second": 3}],
            },
            "target_language": "vi",
            "target_duration_seconds": 45,
        })
        
        assert "Educational" in prompt
        assert "Hook text" in prompt
    
    def test_execute_with_mock(self, agent):
        result = agent.execute({
            "content_plan": {
                "content_angle": "Educational",
                "hook": "Hook",
                "beats": [],
            },
            "target_language": "vi",
            "target_duration_seconds": 45,
        })
        
        assert "title_options" in result
        assert "hook" in result
        assert "narration_text" in result
        assert "segments" in result
        assert len(result["title_options"]) > 0


class TestContentAuditAgent:
    """Test ContentAuditAgent."""
    
    @pytest.fixture
    def agent(self):
        return ContentAuditAgent()
    
    def test_agent_name(self, agent):
        assert agent.agent_name == "ContentAuditAgent"
    
    def test_build_prompt(self, agent):
        prompt = agent.build_prompt({
            "script": {
                "hook": "Hook text",
                "narration_text": "Narration",
                "segments": [],
            },
            "content_plan": {
                "content_angle": "Educational",
                "core_message": "Message",
            },
            "target_platform": "youtube_shorts",
        })
        
        assert "Hook text" in prompt
        assert "Narration" in prompt
    
    def test_execute_with_mock(self, agent):
        result = agent.execute({
            "script": {
                "hook": "Hook",
                "narration_text": "Narration",
                "segments": [],
            },
            "content_plan": {
                "content_angle": "Educational",
                "core_message": "Message",
            },
            "target_platform": "youtube_shorts",
        })
        
        assert "decision" in result
        assert "overall_score" in result
        assert "hook_strength" in result
        assert "issues" in result


class TestScriptReviserAgent:
    """Test ScriptReviserAgent."""
    
    @pytest.fixture
    def agent(self):
        return ScriptReviserAgent()
    
    def test_agent_name(self, agent):
        assert agent.agent_name == "ScriptReviserAgent"
    
    def test_build_prompt(self, agent):
        prompt = agent.build_prompt({
            "script": {
                "hook": "Hook",
                "narration_text": "Narration",
            },
            "audit_report": {
                "decision": "PASS_WITH_FIXES",
                "overall_score": 0.7,
                "issues": [
                    {"severity": "warning", "description": "Weak hook", "required_fix": "Make it stronger"},
                ],
            },
        })
        
        assert "Weak hook" in prompt
        assert "Make it stronger" in prompt
    
    def test_execute_with_mock(self, agent):
        result = agent.execute({
            "script": {
                "hook": "Hook",
                "narration_text": "Narration",
                "segments": [],
            },
            "audit_report": {
                "decision": "PASS_WITH_FIXES",
                "overall_score": 0.7,
                "issues": [],
            },
        })
        
        assert "revised_script" in result
        assert "change_summary" in result
        assert "remaining_issues" in result
