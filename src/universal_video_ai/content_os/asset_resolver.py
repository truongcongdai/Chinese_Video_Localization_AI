"""
Asset resolver for Content OS.

Manages asset discovery, validation, and fallback for video production.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import logging
import os
import time
import hashlib
from pathlib import Path

from .config import CONTENT_OS_ARTIFACT_DIR
from .visual_prompts import clean_text, scene_visual_prompt

logger = logging.getLogger(__name__)

_GEMINI_IMAGE_COOLDOWN_UNTIL = 0.0


class AssetType(str, Enum):
    """Types of assets."""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    STOCK_FOOTAGE = "stock_footage"
    STOCK_IMAGE = "stock_image"


class AssetSource(str, Enum):
    """Asset sources."""
    LOCAL = "local"
    STOCK_API = "stock_api"
    GENERATED = "generated"
    USER_PROVIDED = "user_provided"


@dataclass
class Asset:
    """An asset for video production."""
    asset_id: str
    asset_type: AssetType
    source: AssetSource
    url: str
    local_path: Optional[str]
    metadata: Dict[str, Any]
    license_info: str
    duration_seconds: Optional[float]
    resolution: Optional[str]
    file_size_bytes: Optional[int]
    created_at: float


@dataclass
class AssetManifest:
    """Manifest of all assets for a run."""
    run_id: int
    user_id: int
    assets: List[Asset]
    total_size_bytes: int
    created_at: float
    updated_at: float


class AssetResolver:
    """
    Resolves and manages assets for video production.
    
    Features:
    - Asset discovery from multiple sources
    - Validation and quality checks
    - Fallback mechanisms
    - License tracking
    """
    
    def __init__(self, repository):
        self.repository = repository
    
    def resolve_asset(
        self,
        run_id: int,
        user_id: int,
        asset_type: AssetType,
        description: str,
        preferred_sources: List[AssetSource] = None,
    ) -> Optional[Asset]:
        """
        Resolve an asset based on description and type.
        
        Args:
            run_id: Run ID
            user_id: User ID
            asset_type: Type of asset needed
            description: Description of what's needed
            preferred_sources: Preferred sources to check first
        
        Returns:
            Resolved asset or None
        """
        if preferred_sources is None:
            preferred_sources = [AssetSource.LOCAL, AssetSource.STOCK_API, AssetSource.GENERATED]
        
        for source in preferred_sources:
            asset = self._try_source(source, asset_type, description)
            if asset:
                # Validate asset
                if self._validate_asset(asset):
                    return asset
        
        # Fallback to default placeholder
        return self._get_fallback_asset(asset_type)
    
    def _try_source(
        self, source: AssetSource, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Try to get asset from a specific source."""
        if source == AssetSource.LOCAL:
            return self._search_local_assets(asset_type, description)
        elif source == AssetSource.STOCK_API:
            return self._search_stock_assets(asset_type, description)
        elif source == AssetSource.GENERATED:
            return self._generate_asset(asset_type, description)
        return None
    
    def _search_local_assets(
        self, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Search for local assets."""
        # In a real implementation, this would search local file system
        # For now, return None to trigger fallback
        return None
    
    def _search_stock_assets(
        self, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Search stock asset APIs."""
        if asset_type not in (AssetType.IMAGE, AssetType.STOCK_IMAGE):
            return None

        query = self._stock_query(description)
        pexels_key = os.getenv("PEXELS_API_KEY") or os.getenv("PEXELS_KEY")
        pixabay_key = os.getenv("PIXABAY_API_KEY") or os.getenv("PIXABAY_KEY")

        if pexels_key:
            asset = self._download_pexels_image(query, pexels_key, description)
            if asset:
                return asset
        if pixabay_key:
            asset = self._download_pixabay_image(query, pixabay_key, description)
            if asset:
                return asset
        return None
    
    def _generate_asset(
        self, asset_type: AssetType, description: str
    ) -> Optional[Asset]:
        """Generate a real scene asset, preferring Gemini video when requested."""
        if asset_type == AssetType.VIDEO:
            output_dir = CONTENT_OS_ARTIFACT_DIR / "generated_video_assets"
            output_dir.mkdir(parents=True, exist_ok=True)
            asset_id = f"generated_video_{time.time_ns()}"
            output_path = output_dir / f"{asset_id}.mp4"
            prompt = self._video_prompt(description)
            if self._generate_gemini_video(output_path, prompt):
                duration = self._probe_media_duration(output_path)
                return Asset(
                    asset_id=asset_id, asset_type=AssetType.VIDEO, source=AssetSource.GENERATED,
                    url=str(output_path), local_path=str(output_path),
                    metadata={"description": clean_text(description), "prompt": prompt, "generated": True, "generator": "gemini_video"},
                    license_info="Generated by configured Gemini video provider",
                    duration_seconds=duration, resolution="1080x1920",
                    file_size_bytes=output_path.stat().st_size, created_at=time.time(),
                )
            return None
        if asset_type != AssetType.IMAGE:
            return None

        # Create output directory
        output_dir = CONTENT_OS_ARTIFACT_DIR / "generated_assets"
        output_dir.mkdir(parents=True, exist_ok=True)

        asset_id = f"generated_{time.time_ns()}"
        output_path = output_dir / f"{asset_id}.png"
        prompt = self._image_prompt(description)

        if (
            self._generate_gemini_image(output_path, prompt)
            or self._generate_huggingface_image(output_path, prompt)
            or self._generate_openai_image(output_path, prompt)
        ):
            return Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                source=AssetSource.GENERATED,
                url=str(output_path),
                local_path=str(output_path),
                metadata={"description": clean_text(description), "prompt": prompt, "generated": True, "generator": "ai_image"},
                license_info="Generated by configured image provider",
                duration_seconds=None,
                resolution="1080x1920",
                file_size_bytes=output_path.stat().st_size,
                created_at=time.time(),
            )

        if self._generate_pil_visual(output_path, prompt):
            return Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                source=AssetSource.GENERATED,
                url=str(output_path),
                local_path=str(output_path),
                metadata={"description": clean_text(description), "prompt": prompt, "generated": True, "generator": "local_procedural_visual"},
                license_info="Generated by system",
                duration_seconds=None,
                resolution="1080x1920",
                file_size_bytes=output_path.stat().st_size,
                created_at=time.time(),
            )

        return None


    def _video_prompt(self, description: str) -> str:
        text = clean_text(description) or "A human using an AI learning tool on a smartphone"
        return (
            f"{text}\n"
            "Create a coherent portrait 9:16 cinematic video scene. Show a real human subject performing a clear action, "
            "with natural micro-expressions, hand movement, eye movement, breathing, and believable interaction with objects. "
            "Use subtle camera motion, foreground/background depth, realistic lighting, and no readable text, logos, captions, or watermarks. "
            "Keep the lower 28 percent visually clean for subtitles. The scene must feel like live-action footage, not a poster or static UI mockup."
        )

    def _probe_media_duration(self, path: Path) -> float:
        try:
            import subprocess
            result = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)
            ], capture_output=True, text=True, timeout=30)
            return max(0.0, float(result.stdout.strip() or 0.0))
        except Exception:
            return 0.0

    def _generate_gemini_video(self, output_path: Path, prompt: str) -> bool:
        """Generate a short portrait video through the optional Google GenAI SDK.

        This is deliberately optional: image generation and animated-image fallback
        continue to work when the SDK, model, quota, or region is unavailable.
        """
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
        if not api_key or (os.getenv("CONTENT_OS_ENABLE_GEMINI_VIDEO") or "false").lower() != "true":
            return False
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            model = os.getenv("GEMINI_VIDEO_MODEL") or "veo-3.1-generate-preview"
            config = types.GenerateVideosConfig(
                aspect_ratio="9:16",
                resolution=os.getenv("GEMINI_VIDEO_RESOLUTION") or "720p",
                duration_seconds=int(os.getenv("GEMINI_VIDEO_DURATION_SECONDS") or "8"),
                number_of_videos=1,
            )
            operation = client.models.generate_videos(model=model, prompt=prompt, config=config)
            deadline = time.time() + float(os.getenv("GEMINI_VIDEO_TIMEOUT_SECONDS") or "900")
            while not getattr(operation, "done", False) and time.time() < deadline:
                time.sleep(10)
                operation = client.operations.get(operation)
            if not getattr(operation, "done", False):
                logger.warning("Gemini video generation timed out")
                return False
            response = getattr(operation, "response", None)
            generated = getattr(response, "generated_videos", None) or []
            if not generated:
                logger.warning("Gemini video generation returned no video")
                return False
            video = getattr(generated[0], "video", generated[0])
            try:
                client.files.download(file=video)
            except Exception:
                pass
            if hasattr(video, "save"):
                video.save(str(output_path))
            elif getattr(video, "video_bytes", None):
                output_path.write_bytes(video.video_bytes)
            elif getattr(video, "uri", None):
                import requests
                response = requests.get(video.uri, headers={"x-goog-api-key": api_key}, timeout=180)
                response.raise_for_status()
                output_path.write_bytes(response.content)
            return output_path.exists() and output_path.stat().st_size > 4096 and self._probe_media_duration(output_path) > 0
        except Exception as exc:
            logger.warning("Gemini video generation unavailable, falling back to animated images: %s", exc)
            return False

    def _image_prompt(self, description: str) -> str:
        description = clean_text(description)
        if not description:
            description = scene_visual_prompt("short-form educational content")
        if "Vertical 9:16" not in description:
            description = scene_visual_prompt(description)
        return description

    def _stock_query(self, description: str) -> str:
        text = clean_text(description).lower()
        if "english" in text or "tiếng anh" in text:
            return "student learning english smartphone app"
        if "smartphone" in text or "điện thoại" in text or "phone" in text:
            return "smartphone artificial intelligence app"
        if "ai" in text or "trí tuệ" in text:
            return "artificial intelligence mobile app"
        words = [word for word in clean_text(description).split() if len(word) > 2]
        return " ".join(words[:7]) or "mobile technology"

    def _stock_dir(self) -> Path:
        output_dir = Path("local_data/content_os/stock_assets")
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _download_url_to_file(self, url: str, output_path: Path, headers: Optional[Dict[str, str]] = None) -> bool:
        try:
            import requests

            response = requests.get(url, headers=headers or {}, timeout=60)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "image" not in content_type and not response.content.startswith((b"\xff\xd8", b"\x89PNG")):
                return False
            output_path.write_bytes(response.content)
            return output_path.exists() and output_path.stat().st_size > 1024
        except Exception:
            return False

    def _download_pexels_image(self, query: str, api_key: str, description: str) -> Optional[Asset]:
        try:
            import requests

            response = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "orientation": "portrait", "per_page": 1},
                headers={"Authorization": api_key},
                timeout=30,
            )
            response.raise_for_status()
            photos = response.json().get("photos", [])
            if not photos:
                return None
            photo = photos[0]
            image_url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("portrait") or photo.get("src", {}).get("large")
            if not image_url:
                return None
            asset_id = f"pexels_{photo.get('id')}_{time.time_ns()}"
            output_path = self._stock_dir() / f"{asset_id}.jpg"
            if not self._download_url_to_file(image_url, output_path):
                return None
            return Asset(
                asset_id=asset_id,
                asset_type=AssetType.IMAGE,
                source=AssetSource.STOCK_API,
                url=image_url,
                local_path=str(output_path),
                metadata={"description": clean_text(description), "query": query, "provider": "pexels", "source_id": photo.get("id")},
                license_info="Pexels API media; verify usage terms for your distribution",
                duration_seconds=None,
                resolution="portrait",
                file_size_bytes=output_path.stat().st_size,
                created_at=time.time(),
            )
        except Exception:
            return None

    def _download_pixabay_image(self, query: str, api_key: str, description: str) -> Optional[Asset]:
        try:
            import requests

            response = requests.get(
                "https://pixabay.com/api/",
                params={"key": api_key, "q": query, "image_type": "photo", "orientation": "vertical", "per_page": 3, "safesearch": "true"},
                timeout=30,
            )
            response.raise_for_status()
            hits = response.json().get("hits", [])
            if not hits:
                return None
            hit = hits[0]
            image_url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not image_url:
                return None
            asset_id = f"pixabay_{hit.get('id')}_{time.time_ns()}"
            output_path = self._stock_dir() / f"{asset_id}.jpg"
            if not self._download_url_to_file(image_url, output_path):
                return None
            return Asset(
                asset_id=asset_id,
                asset_type=AssetType.IMAGE,
                source=AssetSource.STOCK_API,
                url=image_url,
                local_path=str(output_path),
                metadata={"description": clean_text(description), "query": query, "provider": "pixabay", "source_id": hit.get("id")},
                license_info="Pixabay API media; verify usage terms for your distribution",
                duration_seconds=None,
                resolution="portrait",
                file_size_bytes=output_path.stat().st_size,
                created_at=time.time(),
            )
        except Exception:
            return None

    def _generate_huggingface_image(self, output_path: Path, prompt: str) -> bool:
        api_key = os.getenv("HUGGINGFACE_API_KEY")
        if not api_key:
            return False
        try:
            import requests

            model_id = os.getenv("HF_IMAGE_MODEL") or "stabilityai/stable-diffusion-xl-base-1.0"
            response = requests.post(
                f"https://api-inference.huggingface.co/models/{model_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"inputs": prompt, "parameters": {"width": 1080, "height": 1920}},
                timeout=180,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "image" not in content_type and not response.content.startswith((b"\xff\xd8", b"\x89PNG")):
                return False
            output_path.write_bytes(response.content)
            return output_path.exists() and output_path.stat().st_size > 1024
        except Exception:
            return False

    def _generate_gemini_image(self, output_path: Path, prompt: str) -> bool:
        global _GEMINI_IMAGE_COOLDOWN_UNTIL
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
        if not api_key:
            return False
        if time.time() < _GEMINI_IMAGE_COOLDOWN_UNTIL:
            logger.warning("Gemini image generation is in quota/error cooldown, using fallback visual")
            return False
        try:
            import base64
            import requests

            model = os.getenv("GEMINI_IMAGE_MODEL") or "gemini-3.1-flash-image"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

            interaction_payload = {
                "model": model,
                "input": prompt,
                "response_format": {
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "aspect_ratio": "9:16",
                    "image_size": os.getenv("GEMINI_IMAGE_SIZE") or "1K",
                },
            }
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers=headers,
                json=interaction_payload,
                timeout=240,
            )
            if response.ok:
                if self._write_first_gemini_image(response.json(), output_path, base64):
                    logger.info("Gemini image generated via interactions: model=%s output=%s", model, output_path)
                    return True
                logger.warning("Gemini interactions returned no image; falling back")
                return False
            if response.status_code == 429:
                _GEMINI_IMAGE_COOLDOWN_UNTIL = time.time() + 15 * 60
                logger.warning("Gemini image quota exceeded; disabling Gemini image attempts for 15 minutes")
                return False
            interaction_error = f"interactions: HTTP {response.status_code} {response.text[:220]}"

            generate_payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "responseFormat": {
                        "image": {
                            "mimeType": "IMAGE_JPEG",
                            "delivery": "INLINE",
                            "aspectRatio": "ASPECT_RATIO_NINE_BY_SIXTEEN",
                        }
                    },
                },
            }
            errors = []
            for version in ("v1", "v1beta"):
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent",
                    headers=headers,
                    json=generate_payload,
                    timeout=240,
                )
                if response.status_code == 429:
                    _GEMINI_IMAGE_COOLDOWN_UNTIL = time.time() + 15 * 60
                    logger.warning("Gemini image quota exceeded; disabling Gemini image attempts for 15 minutes")
                    return False
                if not response.ok:
                    errors.append(f"{version}: HTTP {response.status_code} {response.text[:220]}")
                    continue
                if self._write_first_gemini_image(response.json(), output_path, base64):
                    logger.info("Gemini image generated via generateContent: model=%s output=%s", model, output_path)
                    return True
                errors.append(f"{version}: no inline image returned")
            if errors:
                logger.warning("Gemini image generation failed, falling back: %s", " | ".join([interaction_error, *errors[:2]]))
        except Exception as exc:
            logger.warning("Gemini image generation failed, falling back: %s", exc)
            return False
        return False

    def _write_first_gemini_image(self, payload: Dict[str, Any], output_path: Path, base64_module) -> bool:
        """Find the first base64 image in Gemini response variants and write it."""
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                inline_data = value.get("inlineData") or value.get("inline_data")
                if isinstance(inline_data, dict) and inline_data.get("data"):
                    try:
                        output_path.write_bytes(base64_module.b64decode(inline_data["data"]))
                        return output_path.exists() and output_path.stat().st_size > 1024
                    except Exception:
                        return False
                output_image = value.get("output_image") or value.get("outputImage")
                if isinstance(output_image, dict) and output_image.get("data"):
                    try:
                        output_path.write_bytes(base64_module.b64decode(output_image["data"]))
                        return output_path.exists() and output_path.stat().st_size > 1024
                    except Exception:
                        return False
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        return False

    def _generate_openai_image(self, output_path: Path, prompt: str) -> bool:
        if (os.getenv("USE_DALLE_IMAGES") or "false").lower() != "true":
            return False
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return False
        try:
            import base64
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            result = client.images.generate(
                model=os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1",
                prompt=prompt,
                size="1024x1536",
                n=1,
            )
            item = result.data[0]
            if getattr(item, "b64_json", None):
                output_path.write_bytes(base64.b64decode(item.b64_json))
                return output_path.exists() and output_path.stat().st_size > 1024
            if getattr(item, "url", None):
                return self._download_url_to_file(item.url, output_path)
        except Exception:
            return False
        return False

    def _visual_keywords(self, description: str) -> List[str]:
        text = clean_text(description).lower()
        if "voice waveform" in text or "speaking" in text or "pronunciation" in text or "phát âm" in text:
            return ["Voice Practice", "AI Feedback", "Pronunciation"]
        if "camera" in text or "scan" in text or "translate" in text or "dịch" in text:
            return ["Camera Scan", "Context Meaning", "New Vocabulary"]
        if "keyboard" in text or "grammar" in text or "ngữ pháp" in text:
            return ["Grammar Check", "Better Sentence", "Writing Assist"]
        if "english" in text or "tiếng anh" in text:
            return ["Speaking AI", "Camera Translate", "Smart Flashcards"]
        if "student" in text or "learner" in text or "study" in text:
            return ["Study Goal", "Phone Tutor", "Daily Progress"]
        if "ai" in text:
            return ["AI Assistant", "Automation", "Smart Summary"]
        if "phone" in text or "smartphone" in text or "điện thoại" in text:
            return ["Mobile App", "Quick Workflow", "Daily Practice"]
        return ["Key Idea", "Example", "Takeaway"]

    def _visual_scene_kind(self, description: str) -> str:
        text = clean_text(description).lower()
        if any(token in text for token in ("camera", "scan", "quét", "dịch", "translate", "google lens", "book", "mug")):
            return "camera"
        if any(token in text for token in ("chat", "conversation", "typing", "phản xạ", "giao tiếp")):
            return "chat"
        if any(token in text for token in ("voice", "waveform", "microphone", "dictation", "speaking", "pronunciation", "phát âm", "đọc chính tả")):
            return "voice"
        if any(token in text for token in ("flashcard", "spaced repetition", "vocabulary", "ôn lại", "từ vựng")):
            return "flashcard"
        if any(token in text for token in ("grammar", "keyboard", "suggestion", "ngữ pháp", "bàn phím")):
            return "grammar"
        if any(token in text for token in ("summary", "final", "takeaway", "smiling", "confident", "bắt đầu", "hôm nay")):
            return "final"
        return "hook"

    def _generate_pil_visual(self, output_path, description: str) -> bool:
        """Create a deterministic visual scene for a storyboard prompt."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return False

        try:
            width, height = 1080, 1920
            digest = hashlib.sha256((description or "").encode("utf-8")).digest()
            palettes = [
                ((18, 24, 38), (35, 92, 140), (255, 211, 77)),
                ((24, 35, 30), (51, 118, 86), (255, 244, 214)),
                ((37, 27, 43), (112, 63, 138), (255, 180, 95)),
                ((31, 31, 28), (128, 92, 52), (244, 238, 220)),
                ((17, 31, 43), (44, 124, 141), (238, 246, 255)),
            ]
            bg, mid, accent = palettes[digest[0] % len(palettes)]
            image = Image.new("RGB", (width, height), bg)
            draw = ImageDraw.Draw(image, "RGBA")

            for y in range(height):
                ratio = y / max(1, height - 1)
                color = tuple(round(bg[i] * (1 - ratio) + mid[i] * ratio) for i in range(3))
                draw.line([(0, y), (width, y)], fill=color)

            for index in range(9):
                seed = digest[index + 1]
                x = int((seed / 255) * width)
                y = int(((digest[index + 10] / 255) * 0.62 + 0.08) * height)
                radius = 90 + digest[index + 19] % 190
                fill = (*accent, 24 + digest[index + 5] % 38)
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

            # Large phone mockup / foreground visual. This is intentionally not a text card.
            phone_x0, phone_y0 = 210, 270
            phone_x1, phone_y1 = 870, 1410
            draw.rounded_rectangle(
                (phone_x0, phone_y0, phone_x1, phone_y1),
                radius=72,
                fill=(10, 12, 18, 245),
                outline=(255, 255, 255, 90),
                width=8,
            )
            draw.rounded_rectangle(
                (phone_x0 + 36, phone_y0 + 92, phone_x1 - 36, phone_y1 - 60),
                radius=44,
                fill=(246, 248, 252, 245),
            )

            font_candidates = [
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
            ]
            font_path = next((path for path in font_candidates if Path(path).exists()), None)
            title_font = ImageFont.truetype(font_path, 40) if font_path else ImageFont.load_default()
            small_font = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()

            scene_kind = self._visual_scene_kind(description)
            screen = (phone_x0 + 72, phone_y0 + 150, phone_x1 - 72, phone_y1 - 130)
            sx0, sy0, sx1, sy1 = screen

            if scene_kind == "camera":
                draw.rounded_rectangle((sx0 + 38, sy0 + 70, sx1 - 38, sy1 - 120), radius=30, fill=(34, 43, 55, 238))
                draw.rectangle((sx0 + 96, sy0 + 420, sx1 - 96, sy0 + 560), fill=(245, 238, 220, 245))
                draw.rectangle((sx0 + 116, sy0 + 455, sx1 - 140, sy0 + 474), fill=(120, 138, 155, 180))
                draw.ellipse((sx1 - 210, sy0 + 250, sx1 - 96, sy0 + 360), fill=(185, 122, 68, 245))
                for box in ((sx0 + 92, sy0 + 140, sx1 - 92, sy0 + 300), (sx0 + 128, sy0 + 590, sx1 - 128, sy0 + 710)):
                    draw.rounded_rectangle(box, radius=22, outline=(*accent, 230), width=8)
                draw.line((sx0 + 70, sy0 + 90, sx1 - 70, sy0 + 90), fill=(255, 255, 255, 110), width=5)
                draw.line((sx0 + 70, sy1 - 150, sx1 - 70, sy1 - 150), fill=(255, 255, 255, 110), width=5)
            elif scene_kind == "chat":
                for i in range(6):
                    y = sy0 + 80 + i * 115
                    left = i % 2 == 0
                    box = (sx0 + (48 if left else 190), y, sx1 - (190 if left else 48), y + 76)
                    fill = (231, 244, 255, 250) if left else (*accent, 225)
                    draw.rounded_rectangle(box, radius=28, fill=fill)
                    draw.line((box[0] + 34, y + 38, box[2] - 34, y + 38), fill=(40, 54, 70, 105), width=8)
                draw.ellipse((sx0 + 190, sy1 - 220, sx0 + 430, sy1 + 20), fill=(244, 196, 164, 235))
                draw.rounded_rectangle((sx1 - 260, sy1 - 280, sx1 - 70, sy1 - 120), radius=30, fill=(28, 34, 48, 220))
            elif scene_kind == "voice":
                cx = (sx0 + sx1) // 2
                cy = sy0 + 430
                draw.rounded_rectangle((cx - 70, cy - 170, cx + 70, cy + 110), radius=60, fill=(*accent, 235))
                draw.rectangle((cx - 26, cy + 100, cx + 26, cy + 210), fill=(*accent, 235))
                draw.arc((cx - 165, cy - 60, cx + 165, cy + 260), 20, 160, fill=(35, 45, 62, 230), width=18)
                for i in range(12):
                    x = sx0 + 70 + i * 40
                    h = 60 + digest[(i + 7) % len(digest)] % 240
                    draw.rounded_rectangle((x, sy1 - 250 - h, x + 18, sy1 - 250 + h), radius=9, fill=(*mid, 215))
            elif scene_kind == "flashcard":
                for i in range(4):
                    offset = i * 46
                    draw.rounded_rectangle((sx0 + 90 + offset, sy0 + 150 + offset, sx1 - 140 + offset, sy0 + 470 + offset), radius=38, fill=(255, 255, 255, 245), outline=(*mid, 120), width=4)
                    draw.line((sx0 + 150 + offset, sy0 + 255 + offset, sx1 - 220 + offset, sy0 + 255 + offset), fill=(*accent, 220), width=12)
                for i in range(7):
                    x = sx0 + 95 + i * 68
                    draw.rounded_rectangle((x, sy1 - 210, x + 42, sy1 - 80), radius=18, fill=(*accent, 180 + (i % 2) * 50))
            elif scene_kind == "grammar":
                for row in range(4):
                    for col in range(5):
                        x = sx0 + 52 + col * 96
                        y = sy1 - 380 + row * 74
                        draw.rounded_rectangle((x, y, x + 72, y + 48), radius=13, fill=(220, 226, 235, 245))
                draw.rounded_rectangle((sx0 + 70, sy0 + 180, sx1 - 70, sy0 + 310), radius=28, fill=(228, 255, 235, 245), outline=(40, 180, 112, 210), width=5)
                draw.line((sx0 + 120, sy0 + 245, sx1 - 120, sy0 + 245), fill=(40, 100, 80, 130), width=10)
            else:
                draw.ellipse((sx0 + 110, sy0 + 120, sx0 + 310, sy0 + 320), fill=(244, 196, 164, 245))
                draw.rounded_rectangle((sx0 + 80, sy0 + 315, sx0 + 340, sy0 + 690), radius=80, fill=(*accent, 215))
                draw.rounded_rectangle((sx1 - 285, sy0 + 230, sx1 - 90, sy0 + 610), radius=36, fill=(20, 28, 42, 240))
                draw.rounded_rectangle((sx1 - 260, sy0 + 270, sx1 - 115, sy0 + 560), radius=24, fill=(245, 248, 255, 245))
                for i in range(3):
                    draw.ellipse((sx1 - 225 + i * 46, sy0 + 380, sx1 - 195 + i * 46, sy0 + 410), fill=(*mid, 220))

            # Decorative desk elements that make the fallback feel like a scene.
            draw.rounded_rectangle((120, 1480, 960, 1660), radius=36, fill=(0, 0, 0, 70))
            draw.rectangle((180, 1538, 420, 1588), fill=(255, 255, 255, 210))
            draw.rectangle((190, 1598, 390, 1620), fill=(*accent, 210))
            draw.ellipse((710, 1515, 855, 1660), fill=(255, 255, 255, 210))
            draw.arc((735, 1540, 830, 1635), 20, 340, fill=(*mid, 230), width=12)
            draw.text((118, height - 220), "AI visual scene", fill=(*accent, 230), font=small_font)
            image.save(output_path, "PNG")
            return output_path.exists()
        except Exception:
            return False

    def _validate_asset(self, asset: Asset) -> bool:
        """Validate an asset meets quality requirements."""
        # Basic validation checks
        if not asset.url and not asset.local_path:
            return False

        if asset.asset_type == AssetType.VIDEO:
            if not asset.duration_seconds or asset.duration_seconds <= 0:
                return False

        if asset.asset_type in [AssetType.IMAGE, AssetType.VIDEO]:
            if not asset.resolution:
                return False

        return True

    def _get_fallback_asset(self, asset_type: AssetType) -> Asset:
        """Get a fallback placeholder asset."""
        return Asset(
            asset_id=f"fallback_{asset_type}_{int(time.time())}",
            asset_type=asset_type,
            source=AssetSource.GENERATED,
            url=f"https://placeholder.com/{asset_type}.png",
            local_path=None,
            metadata={"fallback": True, "description": "Placeholder asset"},
            license_info="Public domain placeholder",
            duration_seconds=5.0 if asset_type == AssetType.VIDEO else None,
            resolution="1920x1080" if asset_type in [AssetType.IMAGE, AssetType.VIDEO] else None,
            file_size_bytes=102400,
            created_at=time.time(),
        )

    def create_manifest(
        self, run_id: int, user_id: int, assets: List[Asset]
    ) -> AssetManifest:
        """
        Create an asset manifest for a run.

        Args:
            run_id: Run ID
            user_id: User ID
            assets: List of assets

        Returns:
            Asset manifest
        """
        total_size = sum(a.file_size_bytes or 0 for a in assets)

        manifest = AssetManifest(
            run_id=run_id,
            user_id=user_id,
            assets=assets,
            total_size_bytes=total_size,
            created_at=time.time(),
            updated_at=time.time(),
        )

        # Store as artifact
        self._store_manifest(manifest)

        return manifest

    def _store_manifest(self, manifest: AssetManifest):
        """Store manifest as artifact."""
        data = {
            "run_id": manifest.run_id,
            "user_id": manifest.user_id,
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "asset_type": a.asset_type.value,
                    "source": a.source.value,
                    "url": a.url,
                    "local_path": a.local_path,
                    "metadata": a.metadata,
                    "license_info": a.license_info,
                    "duration_seconds": a.duration_seconds,
                    "resolution": a.resolution,
                    "file_size_bytes": a.file_size_bytes,
                    "created_at": a.created_at,
                }
                for a in manifest.assets
            ],
            "total_size_bytes": manifest.total_size_bytes,
            "created_at": manifest.created_at,
            "updated_at": manifest.updated_at,
        }

        self.repository.create_artifact(
            run_id=manifest.run_id,
            user_id=manifest.user_id,
            artifact_type="asset_manifest",
            version=1,
            schema_version="1.0",
            path=f"/manifests/{manifest.run_id}.json",
            checksum="",
            metadata=data,
            created_by_agent="AssetResolver",
        )

    def get_manifest(
        self, run_id: int, user_id: int
    ) -> Optional[AssetManifest]:
        """Get asset manifest for a run."""
        artifacts = self.repository.list_artifacts(run_id)

        for artifact in artifacts:
            if artifact.artifact_type == "asset_manifest":
                try:
                    data = artifact.metadata if hasattr(artifact, 'metadata') else {}
                    if data:
                        # Convert asset dicts back to Asset objects
                        asset_data = data.get("assets", [])
                        assets = [
                            Asset(
                                asset_id=a["asset_id"],
                                asset_type=AssetType(a["asset_type"]),
                                source=AssetSource(a["source"]),
                                url=a["url"],
                                local_path=a["local_path"],
                                metadata=a["metadata"],
                                license_info=a["license_info"],
                                duration_seconds=a["duration_seconds"],
                                resolution=a["resolution"],
                                file_size_bytes=a["file_size_bytes"],
                                created_at=a["created_at"],
                            )
                            for a in asset_data
                        ]
                        data["assets"] = assets
                        return AssetManifest(**data)
                except (TypeError, KeyError, ValueError):
                    continue

        return None