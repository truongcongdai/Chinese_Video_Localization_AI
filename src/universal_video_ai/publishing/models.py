from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PublishingPackConfig:
    """Per-job configuration for the Reup AI Publishing Pack.

    The feature is deliberately optional and best-effort. A failure in this
    post-render step must never invalidate an otherwise usable localized video.
    """

    enabled: bool = False
    channel_profile: str = "van_diep_studio"
    channel_name: str = "Vạn Diệp Studio"
    platforms: List[str] = field(default_factory=lambda: ["youtube", "facebook"])
    style: str = "balanced"  # search | curiosity | balanced
    edit_level: str = "balanced"  # light | balanced | deep
    provider: str = "auto"  # auto | gemini | openai | ollama | none
    generate_thumbnails: bool = True
    thumbnail_count: int = 3
    generate_publish_ready_video: bool = True
    use_publish_ready_for_social_publish: bool = True
    playlist_url: Optional[str] = None
    custom_instructions: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "PublishingPackConfig":
        if not raw:
            return cls()
        allowed = set(cls.__dataclass_fields__)
        values = {key: value for key, value in raw.items() if key in allowed}
        return cls(**values).normalized()

    def normalized(self) -> "PublishingPackConfig":
        profile = str(self.channel_profile or "van_diep_studio").strip().lower()
        channel_name = " ".join(str(self.channel_name or "Vạn Diệp Studio").split())[:80]
        style = str(self.style or "balanced").strip().lower()
        if style not in {"search", "curiosity", "balanced"}:
            style = "balanced"
        edit_level = str(self.edit_level or "balanced").strip().lower()
        if edit_level not in {"light", "balanced", "deep"}:
            edit_level = "balanced"
        provider = str(self.provider or "auto").strip().lower()
        if provider not in {"auto", "gemini", "openai", "ollama", "none"}:
            provider = "auto"
        platforms: list[str] = []
        for item in self.platforms or []:
            value = str(item or "").strip().lower()
            if value in {"youtube", "facebook"} and value not in platforms:
                platforms.append(value)
        if not platforms:
            platforms = ["youtube"]
        playlist_url = str(self.playlist_url or "").strip() or None
        custom = str(self.custom_instructions or "").strip()[:4000] or None
        return replace(
            self,
            enabled=bool(self.enabled),
            channel_profile=profile,
            channel_name=channel_name,
            platforms=platforms,
            style=style,
            edit_level=edit_level,
            provider=provider,
            generate_thumbnails=bool(self.generate_thumbnails),
            thumbnail_count=max(1, min(3, int(self.thumbnail_count or 3))),
            generate_publish_ready_video=bool(self.generate_publish_ready_video),
            use_publish_ready_for_social_publish=bool(self.use_publish_ready_for_social_publish),
            playlist_url=playlist_url,
            custom_instructions=custom,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self.normalized())
