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
        assert router.provider in {"auto", "gemini", "ollama"}  # Env-dependent default
        
        router2 = LLMRouter(provider="openai", model="gpt-4")
        assert router2.provider == "openai"
        assert router2.model == "gpt-4"

    def test_auto_provider_prefers_gemini_when_key_exists(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        router = LLMRouter(provider="auto")
        assert router._effective_provider() == "gemini"

    def test_auto_provider_falls_back_to_ollama_without_gemini_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
        router = LLMRouter(provider="auto", api_key="")
        assert router._effective_provider() == "ollama"
    
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

    def test_gemini_structured_output(self, monkeypatch):
        """Gemini provider should request JSON output and parse it."""
        class FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": '{"ok": true, "items": [1]}'},
                                ]
                            }
                        }
                    ]
                }

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResponse()

        import requests

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(requests, "post", fake_post)

        router = LLMRouter(provider="gemini", model="gemini-test")
        result = router.invoke("Return JSON", output_schema=object)

        assert result["ok"] is True
        assert "gemini-test:generateContent" in captured["url"]
        assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"


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

    def test_validate_adds_missing_topic(self, agent):
        agent._context = {"topic": "AI học tiếng Anh"}
        result = agent.validate_output({
            "expanded_keywords": ["AI speaking"],
            "detected_trends": [],
            "warnings": [],
        })
        assert result["topic"] == "AI học tiếng Anh"


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
        assert all(
            not segment["visual_instruction"].lower().startswith("show ")
            for segment in result["segments"]
        )
        assert any("Vertical 9:16" in segment["visual_instruction"] for segment in result["segments"])


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

    def test_validate_normalizes_segment_time_aliases(self, agent):
        agent._context = {
            "script": {
                "title_options": ["Old"],
                "hook": "Old hook",
                "narration_text": "Old narration",
                "segments": [
                    {
                        "segment_id": "seg1",
                        "start_second": 0,
                        "end_second": 5,
                        "narration": "Old",
                        "subtitle_text": "Old",
                        "visual_instruction": "Vertical 9:16 realistic scene",
                    }
                ],
                "description": "",
                "hashtags": [],
                "estimated_duration_seconds": 45,
                "source_attributions": [],
            }
        }
        result = agent.validate_output({
            "revised_script": {
                "title_options": ["New"],
                "hook": "New hook",
                "narration_text": "New narration",
                "segments": [
                    {
                        "time_start": 0.0,
                        "time_end": 5.0,
                        "narration": "New narration",
                        "subtitle": "New subtitle",
                        "visual_prompt": "phone AI learning English scene",
                    }
                ],
                "description": "desc",
                "hashtags": ["ai"],
                "estimated_duration_seconds": 45,
                "source_attributions": [],
            },
            "change_summary": "Changed",
            "remaining_issues": [],
        })
        segment = result["revised_script"]["segments"][0]
        assert segment["segment_id"] == "seg1"
        assert segment["start_second"] == 0.0
        assert segment["end_second"] == 5.0
        assert "phone AI learning English scene" in segment["visual_instruction"]
        assert "Script context: New narration" in segment["visual_instruction"]
