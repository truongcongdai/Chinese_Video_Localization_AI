"""
ScriptWriterAgent - Generates the final script.

Responsibilities:
- Generate narration script based on content plan
- Create title options
- Write description and hashtags
- Ensure timing matches beats
- Add source attributions
"""

import logging
from typing import Any, Dict

from pydantic import BaseModel

from .base import BaseAgent
from .llm_router import _parse_json_content
from ..schemas import GeneratedScript, ScriptSegment
from ..visual_prompts import clean_text, infer_topic, normalize_generated_script, scene_visual_prompt

logger = logging.getLogger(__name__)


class ScriptWriterAgent(BaseAgent):
    """Agent for script generation."""

    @property
    def agent_name(self) -> str:
        return "ScriptWriterAgent"

    @property
    def output_schema(self) -> type[BaseModel]:
        return GeneratedScript

    def build_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for script writing."""
        content_plan = context.get("content_plan", {})
        target_language = context.get("target_language", "vi")
        target_duration = context.get("target_duration_seconds", 45)
        user_instructions = context.get("user_instructions", "")

        beats_text = "\n".join(
            [
                f"- Beat {b.get('order', 0)}: {b.get('start_second', 0)}-{b.get('end_second', 0)}s - "
                f"{b.get('purpose', '')}: {b.get('narration_goal', '')}"
                for b in content_plan.get("beats", [])
            ]
        )

        return f"""You are a script writer for short-form vertical video content.

Target language: {target_language}
Target duration: {target_duration} seconds

Content plan:
- Angle: {content_plan.get('content_angle', '')}
- Hook: {content_plan.get('hook', '')}
- Core message: {content_plan.get('core_message', '')}
- Target audience: {content_plan.get('target_audience', '')}

Beats:
{beats_text}

User instructions: {user_instructions}

Generate a complete short-video script. Requirements:
- Write natural narration in the target language.
- subtitle_text must be a readable caption for the segment, not a short headline.
- For subtitle_text use 8-18 words, preserve the main meaning of narration, and avoid cutting the sentence too aggressively.
- Each segment must include a concrete visual_instruction suitable for both image generation and short AI-video generation.
- Every scene should contain a believable human presence whenever the topic allows it: a named role such as student, customer, creator, parent, employee, or expert.
- Describe a visible human action, facial emotion, hand movement, eye direction, interaction with a real object, and a beginning-to-end micro-action for the scene.
- visual_instruction must specify subject continuity, setting, action, camera angle, camera movement, foreground/background depth, lighting, mood, and practical objects.
- Avoid five static product shots. Vary scene grammar: hook close-up, over-the-shoulder demonstration, top-down detail, medium reaction shot, and confident closing shot.
- Make the narration sound human: include a relatable pain point, a concrete everyday example, a brief emotional reaction, and a natural conversational CTA.
- Do not use generic visual instructions like "show intro", "show highlights", or "show summary".
- Avoid brand logos and avoid readable text inside generated visuals.
- Leave the lower 28 percent of the frame clean for subtitles.

Format your response as one valid JSON object:
{{
    "title_options": ["Title 1", "Title 2", "Title 3"],
    "hook": "Hook text",
    "narration_text": "Full narration",
    "segments": [
        {{
            "segment_id": "seg1",
            "start_second": 0.0,
            "end_second": 6.0,
            "narration": "narration for this segment",
            "subtitle_text": "short subtitle text",
            "visual_instruction": "Vertical 9:16 live-action scene with a human subject, a clear action, emotion, camera motion, and clean subtitle area ...",
            "source_refs": []
        }}
    ],
    "description": "Video description",
    "hashtags": ["tag1", "tag2"],
    "estimated_duration_seconds": 45.0,
    "source_attributions": []
}}
"""

    def validate_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate script output and repair weak visual instructions."""
        if set(output.keys()) == {"raw_content"}:
            try:
                output = _parse_json_content(output.get("raw_content", ""))
            except Exception:
                logger.warning("LLM returned raw_content only, using agent fallback output")
                return self._mock_output()

        try:
            topic = infer_topic(getattr(self, "_context", {}), output.get("hook", ""), output.get("narration_text", ""))
            result = self.output_schema(**normalize_generated_script(output, topic))
            return result.model_dump()
        except Exception as exc:
            logger.warning("Output validation failed, returning fallback script: %s", exc)
            return self._mock_output()

    def _mock_output(self) -> Dict[str, Any]:
        """Return deterministic fallback output when the LLM cannot produce valid JSON."""
        context = getattr(self, "_context", {})
        topic = context.get("topic", "")
        objective = context.get("objective", "")
        target_language = context.get("target_language", "vi")

        content_theme = clean_text(topic or objective or "nội dung này")
        visual_topic = infer_topic(context, content_theme)

        if target_language == "vi":
            segments = [
                ScriptSegment(
                    segment_id="seg1",
                    start_second=0.0,
                    end_second=6.0,
                    narration="Bạn đang học tiếng Anh bằng điện thoại? Đây là 3 tính năng AI nên thử ngay.",
                    subtitle_text="3 tính năng AI nên thử khi học tiếng Anh bằng điện thoại.",
                    visual_instruction=scene_visual_prompt(visual_topic, "hook smartphone English learning AI", 1),
                ),
                ScriptSegment(
                    segment_id="seg2",
                    start_second=6.0,
                    end_second=17.0,
                    narration="Đầu tiên là luyện nói với AI: bạn nói một câu, ứng dụng phản hồi và sửa phát âm ngay lập tức.",
                    subtitle_text="Luyện nói với AI: phản hồi và sửa phát âm ngay.",
                    visual_instruction=scene_visual_prompt(visual_topic, "AI speaking practice pronunciation feedback", 2),
                ),
                ScriptSegment(
                    segment_id="seg3",
                    start_second=17.0,
                    end_second=30.0,
                    narration="Thứ hai là camera dịch và giải thích từ trong ngữ cảnh, rất hữu ích khi đọc sách hoặc xem phụ đề.",
                    subtitle_text="Camera dịch và giải thích từ trong ngữ cảnh.",
                    visual_instruction=scene_visual_prompt(visual_topic, "camera translation OCR English book subtitles", 3),
                ),
                ScriptSegment(
                    segment_id="seg4",
                    start_second=30.0,
                    end_second=45.0,
                    narration="Cuối cùng là flashcard thông minh: AI chọn đúng từ bạn hay quên để ôn lại vào thời điểm phù hợp.",
                    subtitle_text="Flashcard AI nhắc lại đúng từ bạn hay quên.",
                    visual_instruction=scene_visual_prompt(visual_topic, "AI flashcard spaced repetition vocabulary review", 4),
                ),
            ]
            return GeneratedScript(
                title_options=[
                    f"Review {content_theme}",
                    "3 tính năng AI giúp học tiếng Anh hiệu quả hơn",
                    f"Cách dùng {content_theme} trên điện thoại",
                ],
                hook=segments[0].narration,
                narration_text=" ".join(segment.narration for segment in segments),
                segments=segments,
                description=f"Khám phá {content_theme}: luyện nói với AI, camera dịch ngữ cảnh và flashcard thông minh.",
                hashtags=[content_theme.lower().replace(" ", ""), "review", "ai", "hoctienganh", "congnghe"],
                estimated_duration_seconds=45.0,
                source_attributions=[],
            ).model_dump()

        segments = [
            ScriptSegment(
                segment_id="seg1",
                start_second=0.0,
                end_second=6.0,
                narration=f"Here is a practical look at {content_theme}.",
                subtitle_text=f"A practical look at {content_theme}.",
                visual_instruction=scene_visual_prompt(visual_topic, "intro", 1),
            ),
            ScriptSegment(
                segment_id="seg2",
                start_second=6.0,
                end_second=18.0,
                narration="First, focus on the feature that saves the most time in everyday use.",
                subtitle_text="Start with the feature that saves the most time.",
                visual_instruction=scene_visual_prompt(visual_topic, "main benefit", 2),
            ),
            ScriptSegment(
                segment_id="seg3",
                start_second=18.0,
                end_second=32.0,
                narration="Next, compare how it works before and after AI assistance.",
                subtitle_text="Compare the before-and-after workflow.",
                visual_instruction=scene_visual_prompt(visual_topic, "before after comparison", 3),
            ),
            ScriptSegment(
                segment_id="seg4",
                start_second=32.0,
                end_second=45.0,
                narration="Use it when the task is repetitive, measurable, and easy to verify.",
                subtitle_text="Use it for repetitive tasks you can verify.",
                visual_instruction=scene_visual_prompt(visual_topic, "summary", 4),
            ),
        ]
        return GeneratedScript(
            title_options=[
                f"{content_theme.title()} Review",
                f"Is {content_theme} Worth It?",
                f"Everything About {content_theme.title()}",
            ],
            hook=segments[0].narration,
            narration_text=" ".join(segment.narration for segment in segments),
            segments=segments,
            description=f"Explore {content_theme} with practical examples and clear takeaways.",
            hashtags=[content_theme.lower().replace(" ", ""), "review", "info", "knowledge", "news"],
            estimated_duration_seconds=45.0,
            source_attributions=[],
        ).model_dump()