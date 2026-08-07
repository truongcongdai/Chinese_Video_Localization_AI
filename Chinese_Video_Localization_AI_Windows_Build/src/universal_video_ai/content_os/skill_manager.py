"""
Skills system for Content OS.

Manages skill definitions (markdown files) that provide platform-specific
guidance, formatting rules, and best practices for content creation.
"""
import os
import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillDefinition:
    """A skill definition loaded from markdown."""
    name: str
    platform: str
    content_type: str
    description: str
    guidelines: List[str]
    format_rules: Dict[str, Any]
    examples: List[Dict[str, Any]]
    constraints: List[str]
    metadata: Dict[str, Any]


class SkillLoader:
    """
    Loads and parses skill definitions from markdown files.
    
    Skills are stored as markdown files in:
    - src/universal_video_ai/content_os/skills/{platform}/{content_type}/SKILL.md
    """
    
    def __init__(self, base_path: Optional[str] = None):
        if base_path is None:
            # Default to skills directory relative to this file
            self.base_path = Path(__file__).parent / "skills"
        else:
            self.base_path = Path(base_path)
    
    def load(
        self, platform: str, content_type: str
    ) -> Optional[SkillDefinition]:
        """
        Load a skill definition for a platform and content type.
        
        Args:
            platform: Platform name (e.g., "youtube_shorts", "facebook_reels")
            content_type: Content type (e.g., "trend_decode", "product_showcase")
        
        Returns:
            SkillDefinition or None if not found
        """
        skill_path = self.base_path / platform / content_type / "SKILL.md"
        
        if not skill_path.exists():
            return None
        
        content = skill_path.read_text(encoding="utf-8")
        return self._parse_skill(content, platform, content_type)
    
    def load_all(self) -> Dict[str, SkillDefinition]:
        """
        Load all available skill definitions.
        
        Returns:
            Dict mapping "{platform}_{content_type}" to SkillDefinition
        """
        skills = {}
        
        if not self.base_path.exists():
            return skills
        
        for platform_dir in self.base_path.iterdir():
            if not platform_dir.is_dir():
                continue
            
            platform = platform_dir.name
            for content_type_dir in platform_dir.iterdir():
                if not content_type_dir.is_dir():
                    continue
                
                content_type = content_type_dir.name
                skill = self.load(platform, content_type)
                if skill:
                    key = f"{platform}_{content_type}"
                    skills[key] = skill
        
        return skills
    
    def _parse_skill(
        self, content: str, platform: str, content_type: str
    ) -> SkillDefinition:
        """
        Parse skill markdown content into a SkillDefinition.
        
        Expected format:
        ```markdown
        # Skill Name
        
        Description of the skill.
        
        ## Guidelines
        - Guideline 1
        - Guideline 2
        
        ## Format Rules
        ```json
        {"duration": 60, "aspect_ratio": "9:16"}
        ```
        
        ## Examples
        ### Example 1
        Description
        ```json
        {"title": "...", "script": "..."}
        ```
        
        ## Constraints
        - Constraint 1
        - Constraint 2
        
        ## Metadata
        ```json
        {"difficulty": "medium", "tags": ["viral", "trending"]}
        ```
        ```
        """
        lines = content.split("\n")
        
        name = content_type.replace("_", " ").title()
        description = ""
        guidelines = []
        format_rules = {}
        examples = []
        constraints = []
        metadata = {}
        
        current_section = None
        current_example = None
        json_buffer = []
        
        for line in lines:
            # Section headers
            if line.startswith("# "):
                current_section = "name"
                name = line[2:].strip()
            elif line.startswith("## "):
                current_section = line[3:].strip().lower().replace(" ", "_")
                json_buffer = []
            elif line.startswith("### "):
                if current_section == "examples":
                    # Save previous example if any
                    if current_example:
                        examples.append(current_example)
                    current_example = {"name": line[4:].strip(), "data": {}}
            elif line.strip() == "```json":
                # Start JSON block
                continue
            elif line.strip() == "```":
                # End JSON block
                if json_buffer:
                    try:
                        json_data = json.loads("\n".join(json_buffer))
                        if current_section == "format_rules":
                            format_rules = json_data
                        elif current_section == "metadata":
                            metadata = json_data
                        elif current_section == "examples" and current_example:
                            current_example["data"] = json_data
                    except json.JSONDecodeError:
                        pass
                    json_buffer = []
            elif line.strip().startswith("- "):
                # List item
                item = line.strip()[2:]
                if current_section == "guidelines":
                    guidelines.append(item)
                elif current_section == "constraints":
                    constraints.append(item)
            else:
                # Regular content
                if current_section == "description" or current_section == "name":
                    description += line + "\n"
                elif line.strip().startswith("```json"):
                    continue
                elif line.strip() == "```":
                    continue
                elif current_section in ["format_rules", "metadata", "examples"]:
                    json_buffer.append(line)
        
        # Save last example
        if current_example:
            examples.append(current_example)
        
        return SkillDefinition(
            name=name,
            platform=platform,
            content_type=content_type,
            description=description.strip(),
            guidelines=guidelines,
            format_rules=format_rules,
            examples=examples,
            constraints=constraints,
            metadata=metadata,
        )
    
    def get_available_platforms(self) -> List[str]:
        """Get list of available platforms."""
        if not self.base_path.exists():
            return []
        
        return [d.name for d in self.base_path.iterdir() if d.is_dir()]
    
    def get_available_content_types(self, platform: str) -> List[str]:
        """Get list of available content types for a platform."""
        platform_path = self.base_path / platform
        if not platform_path.exists():
            return []
        
        return [d.name for d in platform_path.iterdir() if d.is_dir()]


class SkillManager:
    """
    Manages skill definitions and provides formatted guidance for agents.
    """
    
    def __init__(self, loader: Optional[SkillLoader] = None):
        self.loader = loader or SkillLoader()
        self._cache: Dict[str, SkillDefinition] = {}
    
    def get_skill(
        self, platform: str, content_type: str
    ) -> Optional[SkillDefinition]:
        """
        Get a skill definition with caching.
        
        Args:
            platform: Platform name
            content_type: Content type
        
        Returns:
            SkillDefinition or None
        """
        key = f"{platform}_{content_type}"
        
        if key not in self._cache:
            self._cache[key] = self.loader.load(platform, content_type)
        
        return self._cache[key]
    
    def format_for_agent(
        self, platform: str, content_type: str
    ) -> Optional[str]:
        """
        Format skill definition as a string for LLM agents.
        
        Args:
            platform: Platform name
            content_type: Content type
        
        Returns:
            Formatted skill string or None
        """
        skill = self.get_skill(platform, content_type)
        if not skill:
            return None
        
        sections = []
        sections.append(f"## Skill: {skill.name}")
        sections.append(f"Platform: {skill.platform}")
        sections.append(f"Content Type: {skill.content_type}")
        
        if skill.description:
            sections.append(f"\n{skill.description}")
        
        if skill.guidelines:
            sections.append("\n### Guidelines")
            for guideline in skill.guidelines:
                sections.append(f"- {guideline}")
        
        if skill.format_rules:
            sections.append("\n### Format Rules")
            sections.append(f"```json")
            sections.append(json.dumps(skill.format_rules, indent=2))
            sections.append(f"```")
        
        if skill.constraints:
            sections.append("\n### Constraints")
            for constraint in skill.constraints:
                sections.append(f"- {constraint}")
        
        if skill.examples:
            sections.append("\n### Examples")
            for example in skill.examples[:2]:  # Limit to 2 examples
                sections.append(f"\n#### {example['name']}")
                sections.append(f"```json")
                sections.append(json.dumps(example['data'], indent=2))
                sections.append(f"```")
        
        return "\n".join(sections)
    
    def validate_against_skill(
        self, content: Dict[str, Any], platform: str, content_type: str
    ) -> List[str]:
        """
        Validate content against skill constraints and rules.
        
        Args:
            content: Content to validate
            platform: Platform name
            content_type: Content type
        
        Returns:
            List of validation errors (empty if valid)
        """
        skill = self.get_skill(platform, content_type)
        if not skill:
            return []
        
        errors = []
        
        # Check format rules
        for key, expected_value in skill.format_rules.items():
            if key in content:
                actual_value = content[key]
                if isinstance(expected_value, (int, float)):
                    if isinstance(actual_value, (int, float)):
                        if actual_value > expected_value:
                            errors.append(
                                f"{key} exceeds maximum: {actual_value} > {expected_value}"
                            )
                elif isinstance(expected_value, list):
                    if actual_value not in expected_value:
                        errors.append(
                            f"{key} must be one of: {expected_value}, got: {actual_value}"
                        )
        
        return errors
