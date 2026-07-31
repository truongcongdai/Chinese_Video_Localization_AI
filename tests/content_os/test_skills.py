"""
Tests for skills system.
"""
import pytest
import json
from pathlib import Path
from universal_video_ai.content_os.skill_manager import SkillLoader, SkillManager, SkillDefinition


@pytest.fixture
def temp_skills_dir(tmp_path):
    """Temporary skills directory with test skill files."""
    skills_dir = tmp_path / "skills"
    
    # Create youtube_shorts/trend_decode/SKILL.md
    youtube_dir = skills_dir / "youtube_shorts" / "trend_decode"
    youtube_dir.mkdir(parents=True)
    
    skill_content = """# Trend Decode

Creates engaging videos explaining trending topics.

## Guidelines
- Start with a strong hook
- Explain the trend clearly
- Add personal perspective
- End with call to action

## Format Rules
```json
{"duration": 60, "aspect_ratio": "9:16", "max_text_lines": 3}
```

## Examples
### Viral Example
High-performing trend decode video
```json
{"title": "AI Revolution", "hook": "AI is changing everything", "pacing": "fast"}
```

### Educational Example
Educational trend decode
```json
{"title": "Quantum Computing", "hook": "What if computers could think?", "pacing": "moderate"}
```

## Constraints
- No clickbait titles
- Accurate information only
- Keep under 60 seconds

## Metadata
```json
{"difficulty": "medium", "tags": ["viral", "trending", "educational"]}
```
"""
    
    (youtube_dir / "SKILL.md").write_text(skill_content)
    
    return skills_dir


class TestSkillLoader:
    """Test skill loader."""
    
    def test_load_skill(self, temp_skills_dir):
        """Test loading a skill definition."""
        loader = SkillLoader(str(temp_skills_dir))
        skill = loader.load("youtube_shorts", "trend_decode")
        
        assert skill is not None
        assert skill.name == "Trend Decode"
        assert skill.platform == "youtube_shorts"
        assert skill.content_type == "trend_decode"
        assert len(skill.guidelines) == 4
        assert skill.format_rules["duration"] == 60
        assert len(skill.examples) == 2
        assert len(skill.constraints) == 3
    
    def test_load_nonexistent_skill(self, temp_skills_dir):
        """Test loading a skill that doesn't exist."""
        loader = SkillLoader(str(temp_skills_dir))
        skill = loader.load("nonexistent", "skill")
        
        assert skill is None
    
    def test_load_all_skills(self, temp_skills_dir):
        """Test loading all available skills."""
        loader = SkillLoader(str(temp_skills_dir))
        skills = loader.load_all()
        
        assert len(skills) == 1
        assert "youtube_shorts_trend_decode" in skills
    
    def test_get_available_platforms(self, temp_skills_dir):
        """Test getting available platforms."""
        loader = SkillLoader(str(temp_skills_dir))
        platforms = loader.get_available_platforms()
        
        assert len(platforms) == 1
        assert "youtube_shorts" in platforms
    
    def test_get_available_content_types(self, temp_skills_dir):
        """Test getting available content types for a platform."""
        loader = SkillLoader(str(temp_skills_dir))
        content_types = loader.get_available_content_types("youtube_shorts")
        
        assert len(content_types) == 1
        assert "trend_decode" in content_types


class TestSkillManager:
    """Test skill manager."""
    
    def test_get_skill_with_cache(self, temp_skills_dir):
        """Test getting skill with caching."""
        loader = SkillLoader(str(temp_skills_dir))
        manager = SkillManager(loader)
        
        # First call
        skill1 = manager.get_skill("youtube_shorts", "trend_decode")
        # Second call (should use cache)
        skill2 = manager.get_skill("youtube_shorts", "trend_decode")
        
        assert skill1 is not None
        assert skill1 is skill2  # Same object due to caching
    
    def test_format_for_agent(self, temp_skills_dir):
        """Test formatting skill for agent consumption."""
        loader = SkillLoader(str(temp_skills_dir))
        manager = SkillManager(loader)
        
        formatted = manager.format_for_agent("youtube_shorts", "trend_decode")
        
        assert formatted is not None
        assert "Skill: Trend Decode" in formatted
        assert "Platform: youtube_shorts" in formatted
        assert "Guidelines" in formatted
        assert "Format Rules" in formatted
        assert "Constraints" in formatted
    
    def test_validate_against_skill(self, temp_skills_dir):
        """Test validating content against skill rules."""
        loader = SkillLoader(str(temp_skills_dir))
        manager = SkillManager(loader)
        
        # Valid content
        valid_content = {"duration": 45, "aspect_ratio": "9:16"}
        errors = manager.validate_against_skill(
            valid_content, "youtube_shorts", "trend_decode"
        )
        assert len(errors) == 0
        
        # Invalid content (duration exceeds max)
        invalid_content = {"duration": 90, "aspect_ratio": "9:16"}
        errors = manager.validate_against_skill(
            invalid_content, "youtube_shorts", "trend_decode"
        )
        assert len(errors) > 0
        assert "duration" in errors[0].lower()
