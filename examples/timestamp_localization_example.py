#!/usr/bin/env python3
"""
Ví dụ minh họa workflow Video Localization với Timestamps

Workflow:
1. Detect text gốc từ video theo timestamps (OCR)
2. Tách câu theo timestamps (0-3s, 3-6s, ...)
3. Tạo ô trắng che text gốc (blur box) theo vị trí detect
4. Dịch text sang tiếng Việt
5. Chèn subtitle dịch vào ô trắng đó
6. Tạo voice đọc theo timestamps tương ứng
7. Render video final với subtitle dịch và voice mới

Example usage:
    python examples/timestamp_localization_example.py
"""

import asyncio
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
import logging

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from universal_video_ai.timeline.service import TimelineService, TimelineSegment
from universal_video_ai.translate.translator import TranslatorFactory, TranslatorConfig
from universal_video_ai.tts.backend import EdgeTTSBackend
from universal_video_ai.render.renderer import Renderer, RenderConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class TextRegion:
    """Vị trí text detect được trong video"""
    start_time: float  # seconds
    end_time: float  # seconds
    text: str  # text gốc detect được
    x: int  # tọa độ x
    y: int  # tọa độ y
    width: int  # chiều rộng
    height: int  # chiều cao
    translated_text_en: Optional[str] = None  # text dịch tiếng Anh
    translated_text_vi: Optional[str] = None  # text dịch tiếng Việt


class TimestampLocalizationExample:
    """Ví dụ minh họa workflow localization với timestamps"""
    
    def __init__(self, target_language: str = "vi"):
        """
        Args:
            target_language: Ngôn ngữ đích ('en' cho tiếng Anh, 'vi' cho tiếng Việt)
        """
        self.target_language = target_language
        self.timeline_service = TimelineService()
        self.translator = TranslatorFactory.create(
            config=TranslatorConfig(
                provider="noop",  # Sử dụng noop cho ví dụ, thay bằng "google" hoặc "deepl" cho production
                src_lang="zh",  # Chinese
                dest_lang=target_language  # English hoặc Vietnamese
            )
        )
        self.tts_backend = EdgeTTSBackend()
        self.renderer = Renderer(
            config=RenderConfig(
                blur_text=True,
                blur_box=None,  # Sẽ set động theo từng region
            )
        )
    
    async def detect_text_regions(self, video_path: Path) -> List[TextRegion]:
        """
        Bước 1: Detect text từ video theo timestamps
        
        Trong thực tế, sẽ dùng OCR (PaddleOCR, EasyOCR, Tesseract) để detect text
        và vị trí của text trong từng frame của video.
        
        Đây là ví dụ giả lập với data mẫu.
        """
        logger.info("Bước 1: Detect text regions from video...")
        
        # Ví dụ data mẫu - trong thực tế sẽ dùng OCR
        # 
        # MAPPING TIMESTAMPS (Hỗ trợ 2 ngôn ngữ: EN và VI):
        # Video gốc 0-3s: Text "你好世界" tại vị trí (100, 800, 400, 50)
        #   → Bản final 0-3s (EN): Che text gốc bằng ô trắng, chèn "Hello World"
        #   → Voice EN 0-3s: Đọc "Hello World"
        #   → Bản final 0-3s (VI): Che text gốc bằng ô trắng, chèn "Xin chào thế giới"
        #   → Voice VI 0-3s: Đọc "Xin chào thế giới"
        #
        # Video gốc 3-6s: Text "这是一个测试" tại vị trí (100, 800, 500, 50)
        #   → Bản final 3-6s (EN): Che text gốc bằng ô trắng, chèn "This is a test"
        #   → Voice EN 3-6s: Đọc "This is a test"
        #   → Bản final 3-6s (VI): Che text gốc bằng ô trắng, chèn "Đây là một bài kiểm tra"
        #   → Voice VI 3-6s: Đọc "Đây là một bài kiểm tra"
        #
        # Video gốc 6-9s: Text "视频本地化示例" tại vị trí (100, 800, 600, 50)
        #   → Bản final 6-9s (EN): Che text gốc bằng ô trắng, chèn "Video localization example"
        #   → Voice EN 6-9s: Đọc "Video localization example"
        #   → Bản final 6-9s (VI): Che text gốc bằng ô trắng, chèn "Ví dụ bản địa hóa video"
        #   → Voice VI 6-9s: Đọc "Ví dụ bản địa hóa video"
        
        regions = [
            TextRegion(
                start_time=0.0,
                end_time=3.0,
                text="你好世界",
                x=100,
                y=800,
                width=400,
                height=50,
                translated_text_en="Hello World",
                translated_text_vi="Xin chào thế giới"
            ),
            TextRegion(
                start_time=3.0,
                end_time=6.0,
                text="这是一个测试",
                x=100,
                y=800,
                width=500,
                height=50,
                translated_text_en="This is a test",
                translated_text_vi="Đây là một bài kiểm tra"
            ),
            TextRegion(
                start_time=6.0,
                end_time=9.0,
                text="视频本地化示例",
                x=100,
                y=800,
                width=600,
                height=50,
                translated_text_en="Video localization example",
                translated_text_vi="Ví dụ bản địa hóa video"
            )
        ]
        
        logger.info(f"Detected {len(regions)} text regions")
        logger.info(f"Target language: {self.target_language.upper()}")
        logger.info("MAPPING TIMESTAMPS:")
        for region in regions:
            logger.info(f"  Video gốc [{region.start_time:.1f}s - {region.end_time:.1f}s]: {region.text} at ({region.x},{region.y})")
            if self.target_language == "en":
                logger.info(f"    → Bản final EN [{region.start_time:.1f}s - {region.end_time:.1f}s]: Che text gốc, chèn '{region.translated_text_en}'")
                logger.info(f"    → Voice EN [{region.start_time:.1f}s - {region.end_time:.1f}s]: Đọc '{region.translated_text_en}'")
            else:
                logger.info(f"    → Bản final VI [{region.start_time:.1f}s - {region.end_time:.1f}s]: Che text gốc, chèn '{region.translated_text_vi}'")
                logger.info(f"    → Voice VI [{region.start_time:.1f}s - {region.end_time:.1f}s]: Đọc '{region.translated_text_vi}'")
        
        return regions
    
    def convert_to_timeline_segments(self, regions: List[TextRegion]) -> List[TimelineSegment]:
        """
        Bước 2: Convert text regions sang TimelineSegments
        
        Sử dụng text dịch theo target_language đã chọn
        """
        logger.info(f"Bước 2: Convert to timeline segments (target: {self.target_language.upper()})...")
        
        segments = []
        for region in regions:
            # Chọn text dịch theo target_language
            if self.target_language == "en":
                translated_text = region.translated_text_en or region.text
            else:
                translated_text = region.translated_text_vi or region.text
            
            segment = TimelineSegment(
                start_time=region.start_time,
                end_time=region.end_time,
                text=translated_text
            )
            segments.append(segment)
        
        return segments
    
    async def translate_segments(self, segments: List[TimelineSegment]) -> List[TimelineSegment]:
        """
        Bước 3: Dịch từng segment sang target language (EN hoặc VI)
        
        Lưu ý: Trong ví dụ này, text dịch đã có sẵn trong TextRegion,
        nên hàm này chỉ log thông tin. Trong thực tế, sẽ gọi translator.
        """
        logger.info(f"Bước 3: Translate segments to {self.target_language.upper()}...")
        
        # Trong ví dụ này, text dịch đã có sẵn từ TextRegion
        # Nên chỉ log thông tin và return segments như là
        translated_segments = segments
        
        for segment in segments:
            logger.info(f"  [{segment.start_time:.1f}s - {segment.end_time:.1f}s] {segment.text}")
            logger.info(f"    → Voice {self.target_language.upper()} sẽ đọc từ {segment.start_time:.1f}s đến {segment.end_time:.1f}s")
        
        return translated_segments
    
    def generate_subtitle_file(self, segments: List[TimelineSegment], output_path: Path) -> Path:
        """
        Bước 4: Generate file SRT từ segments
        """
        logger.info(f"Bước 4: Generate SRT subtitle file (target: {self.target_language.upper()})...")
        
        srt_content = self.timeline_service.generate_srt(segments)
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        
        logger.info(f"Subtitle file saved to: {output_path}")
        return output_path
    
    async def generate_tts_audio(self, segments: List[TimelineSegment], output_dir: Path) -> List[Path]:
        """
        Bước 5: Generate TTS audio cho từng segment
        
        QUAN TRỌNG: Voice phải đọc theo timestamps tương ứng với text gốc.
        - Segment 0-3s text dịch → Voice đọc từ 0-3s
        - Segment 3-6s text dịch → Voice đọc từ 3-6s
        - Segment 6-9s text dịch → Voice đọc từ 6-9s
        
        Trong thực tế, sẽ generate audio cho từng segment riêng biệt,
        sau đó dùng ffmpeg để concat theo timestamps.
        """
        logger.info("Bước 5: Generate TTS audio for each segment...")
        logger.info("Voice sẽ đọc theo timestamps tương ứng:")
        
        audio_paths = []
        for idx, segment in enumerate(segments):
            output_path = output_dir / f"segment_{idx:03d}.wav"
            audio_path = await self.tts_backend.synthesize(segment.text, output_path)
            audio_paths.append(audio_path)
            logger.info(f"  Segment {idx} [{segment.start_time:.1f}s - {segment.end_time:.1f}s]: {segment.text}")
            logger.info(f"    → Voice đọc từ {segment.start_time:.1f}s đến {segment.end_time:.1f}s")
        
        return audio_paths
    
    def get_blur_box_for_region(self, region: TextRegion) -> str:
        """
        Tạo blur box string cho một region
        
        Format: "x:y:w:h"
        """
        return f"{region.x}:{region.y}:{region.width}:{region.height}"
    
    async def render_final_video(
        self,
        video_path: Path,
        audio_path: Path,
        subtitle_path: Path,
        regions: List[TextRegion],
        output_path: Path
    ) -> Path:
        """
        Bước 6: Render video final với blur và subtitle
        
        Lưu ý: Hiện tại renderer chỉ hỗ trợ 1 blur box global.
        Để blur nhiều region theo timestamps, cần nâng cấp renderer.
        """
        logger.info("Bước 6: Render final video with blur and subtitles...")
        
        # Trong ví dụ này, sử dụng blur box của region đầu tiên
        # Trong thực tế, cần nâng cấp renderer để blur theo timestamps
        if regions:
            blur_box = self.get_blur_box_for_region(regions[0])
            logger.info(f"Using blur box: {blur_box}")
            
            config = RenderConfig(
                blur_text=True,
                blur_box=blur_box,
                video_codec="libx264",
                crf=23,
                preset="medium"
            )
            renderer = Renderer(config=config)
        else:
            renderer = self.renderer
        
        result = renderer.render(
            video_path=video_path,
            audio_path=audio_path,
            subtitles=subtitle_path,
            output_path=output_path
        )
        
        logger.info(f"Final video rendered to: {result}")
        return result
    
    async def run_workflow(self, video_path: Path, output_dir: Path) -> None:
        """
        Chạy workflow hoàn chỉnh
        """
        logger.info("=" * 60)
        logger.info("TIMESTAMP LOCALIZATION WORKFLOW")
        logger.info("=" * 60)
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Bước 1: Detect text regions
        regions = await self.detect_text_regions(video_path)
        
        # Bước 2: Convert to timeline segments
        segments = self.convert_to_timeline_segments(regions)
        
        # Bước 3: Translate segments
        translated_segments = await self.translate_segments(segments)
        
        # Bước 4: Generate subtitle file
        subtitle_filename = f"translated_{self.target_language}.srt"
        subtitle_path = output_dir / subtitle_filename
        self.generate_subtitle_file(translated_segments, subtitle_path)
        
        # Bước 5: Generate TTS audio (giả lập)
        tts_dir = output_dir / "tts_segments"
        # audio_paths = await self.generate_tts_audio(translated_segments, tts_dir)
        # Trong ví dụ này, giả lập đã có audio
        audio_path = output_dir / "tts_audio.wav"
        logger.info(f"TTS audio (simulated): {audio_path}")
        
        # Bước 6: Render final video
        final_video_path = output_dir / "final_video.mp4"
        # await self.render_final_video(video_path, audio_path, subtitle_path, regions, final_video_path)
        
        logger.info("=" * 60)
        logger.info("WORKFLOW COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Output directory: {output_dir}")
        logger.info(f"Subtitle file: {subtitle_path}")
        logger.info(f"Text regions detected: {len(regions)}")
        logger.info(f"Segments translated: {len(translated_segments)}")


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Video Localization với Timestamps")
    parser.add_argument(
        "--lang",
        choices=["en", "vi"],
        default="vi",
        help="Ngôn ngữ đích: en (tiếng Anh) hoặc vi (tiếng Việt)"
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=Path("/path/to/original/video.mp4"),
        help="Đường dẫn đến video gốc"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/path/to/output"),
        help="Thư mục output"
    )
    
    args = parser.parse_args()
    
    # Tạo example với target language đã chọn
    example = TimestampLocalizationExample(target_language=args.lang)
    
    logger.info(f"Running workflow for target language: {args.lang.upper()}")
    
    # Chạy workflow
    await example.run_workflow(args.video, args.output)


if __name__ == "__main__":
    asyncio.run(main())
