from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

# Generic is the only system-wide default. Channel-specific SEO rules belong
# to the user who created them and are stored in SQLite via the web profile API.
_GENERIC_PROFILE: Dict[str, Any] = {
    "id": "generic_reup",
    "name": "Reup tổng quát",
    "channel_name": "",
    "language": "vi",
    "niche": "Video được biên tập, bản địa hóa và đăng lại theo nội dung thực tế",
    "brand_line": "",
    "audience": "Khán giả phù hợp với nội dung video",
    "category": "Entertainment",
    "made_for_kids": False,
    "default_privacy": "private",
    "primary_keyword_groups": {},
    "base_tags": [],
    "base_hashtags": [],
    "forbidden_generic_keywords": [],
    "title_formula": "Hook mạnh + mâu thuẫn hoặc cú lật + keyword sát nội dung; ưu tiên khiến người xem muốn bấm ngay",
    "thumbnail_rules": {
        "max_words": 4,
        "examples": ["CÚ LẬT CUỐI", "MÀN ĐỐI ĐẦU", "LÊN THẲNG SSS", "THẬT KHÔNG NGỜ", "NÂNG CẤP CỰC GẮT"],
    },
    "description_template": "{episode_summary}\n\n{hashtags}",
    "custom_instructions": "",
}

# Backward compatibility only. New jobs never select this automatically and
# the UI does not expose it globally. Old jobs created by v1/v1.1 can still be
# regenerated reproducibly until the user saves the profile into their own DB.
_LEGACY_VAN_DIEP_PROFILE: Dict[str, Any] = {
    "id": "van_diep_studio",
    "name": "Vạn Diệp Studio",
    "channel_name": "Vạn Diệp Studio",
    "language": "vi",
    "niche": "Review và kể chuyện tu tiên, tiên hiệp, trường sinh, xây dựng gia tộc và tông môn",
    "brand_line": "Một gốc khai tiên lộ, vạn diệp truyền trường sinh.",
    "audience": "Khán giả Việt Nam yêu thích truyện tu tiên, tiên hiệp, hệ thống, gia tộc và trường sinh",
    "category": "Entertainment",
    "made_for_kids": False,
    "default_privacy": "private",
    "primary_keyword_groups": {
        "family": [
            "tu tiên gia tộc", "gia tộc tu tiên", "xây dựng gia tộc tu tiên",
            "gia tộc trường sinh", "gia tộc quật khởi", "hệ thống gia tộc",
            "tộc trưởng tu tiên", "phát triển gia tộc",
        ],
        "longevity": [
            "tu tiên trường sinh", "main trường sinh", "lão tổ trường sinh",
            "trường sinh bất tử", "lão tổ gia tộc", "bế quan vạn năm",
            "tu tiên nhiều thế hệ",
        ],
        "faction": [
            "xây dựng tông môn", "phát triển tông môn", "xây dựng thế lực tu tiên",
            "tiên tộc", "tiên triều", "tông môn quật khởi", "chiêu mộ đệ tử",
        ],
        "format": [
            "review truyện tu tiên", "review truyện tiên hiệp", "truyện tu tiên dài tập",
            "review truyện Trung Quốc", "kể chuyện tu tiên",
        ],
    },
    "base_tags": [
        "Vạn Diệp Studio", "review truyện tu tiên", "tu tiên gia tộc",
        "gia tộc trường sinh", "review truyện tiên hiệp", "xây dựng gia tộc",
        "hệ thống gia tộc", "xây dựng tông môn",
    ],
    "base_hashtags": ["#VạnDiệpStudio", "#TuTiênGiaTộc", "#ReviewTruyện"],
    "forbidden_generic_keywords": [
        "phim hay", "video hay", "giải trí", "trending", "viral", "anime", "game",
    ],
    "title_formula": "Hook rất mạnh + mâu thuẫn/cú lật + hệ thống hoặc thế lực đặc biệt + keyword ngách; ưu tiên cảm giác drama và thăng cấp rõ ràng",
    "thumbnail_rules": {
        "max_words": 5,
        "examples": [
            "LÃO TỔ TRỞ VỀ", "GIA TỘC QUẬT KHỞI", "VẠN NĂM TRƯỜNG SINH",
            "MỘT NGƯỜI NUÔI CẢ TỘC", "TỪ PHÀM TỘC ĐẾN TIÊN TỘC",
            "MÀN ĐỐI ĐẦU", "SỰ THẬT LỘ DIỆN", "CÚ LẬT CUỐI",
        ],
    },
    "description_template": (
        "📖 Tên truyện: {story_name}\n\n"
        "Trong video này, {channel_name} sẽ kể lại hành trình {episode_journey}.\n\n"
        "{episode_summary}\n\n"
        "Đây là series review truyện tu tiên, trường sinh, xây dựng gia tộc, "
        "phát triển tông môn và thế lực.\n\n"
        "▶ Xem trọn bộ series: {playlist_url}\n"
        "▶ Đăng ký {channel_name} để theo dõi tập tiếp theo.\n\n"
        "{hashtags}"
    ),
    "custom_instructions": "",
}


def _list_strings(value: Any, limit: int = 100) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.replace("\n", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = " ".join(str(item or "").split())[:160]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def normalize_channel_profile(raw: Optional[Dict[str, Any]], *, profile_id: str = "custom") -> Dict[str, Any]:
    """Normalize untrusted per-user profile data into the publishing schema."""
    base = deepcopy(_GENERIC_PROFILE)
    raw = dict(raw or {})
    base["id"] = str(raw.get("id") or profile_id or "custom")[:80]
    base["name"] = " ".join(str(raw.get("name") or raw.get("channel_name") or "Hồ sơ kênh").split())[:100]
    base["channel_name"] = " ".join(str(raw.get("channel_name") or "").split())[:100]
    base["language"] = str(raw.get("language") or "vi").strip().lower()[:12] or "vi"
    base["niche"] = " ".join(str(raw.get("niche") or base["niche"]).split())[:1000]
    base["audience"] = " ".join(str(raw.get("audience") or base["audience"]).split())[:1000]
    base["brand_line"] = " ".join(str(raw.get("brand_line") or "").split())[:300]
    base["category"] = " ".join(str(raw.get("category") or "Entertainment").split())[:80]
    base["made_for_kids"] = bool(raw.get("made_for_kids", False))
    privacy = str(raw.get("default_privacy") or "private").strip().lower()
    base["default_privacy"] = privacy if privacy in {"private", "unlisted", "public"} else "private"

    groups: Dict[str, list[str]] = {}
    raw_groups = raw.get("primary_keyword_groups") or {}
    if isinstance(raw_groups, dict):
        for key, values in list(raw_groups.items())[:20]:
            group_name = " ".join(str(key or "keywords").split())[:80] or "keywords"
            items = _list_strings(values, 50)
            if items:
                groups[group_name] = items
    elif raw_groups:
        items = _list_strings(raw_groups, 100)
        if items:
            groups["keywords"] = items
    base["primary_keyword_groups"] = groups
    base["base_tags"] = _list_strings(raw.get("base_tags"), 50)
    hashtags = _list_strings(raw.get("base_hashtags"), 20)
    base["base_hashtags"] = [tag if tag.startswith("#") else f"#{tag.replace(' ', '')}" for tag in hashtags]
    base["forbidden_generic_keywords"] = _list_strings(raw.get("forbidden_generic_keywords"), 50)
    base["title_formula"] = " ".join(str(raw.get("title_formula") or base["title_formula"]).split())[:800]

    thumb = raw.get("thumbnail_rules") if isinstance(raw.get("thumbnail_rules"), dict) else {}
    try:
        max_words = max(2, min(8, int(thumb.get("max_words", 5))))
    except (TypeError, ValueError):
        max_words = 5
    base["thumbnail_rules"] = {
        "max_words": max_words,
        "examples": _list_strings(thumb.get("examples"), 20),
    }
    base["description_template"] = str(raw.get("description_template") or base["description_template"])[:8000]
    base["custom_instructions"] = str(raw.get("custom_instructions") or "")[:4000]
    return base


def generic_channel_profile(channel_name: str = "") -> Dict[str, Any]:
    profile = normalize_channel_profile(_GENERIC_PROFILE, profile_id="generic_reup")
    if channel_name:
        profile["channel_name"] = " ".join(str(channel_name).split())[:100]
    return profile


def legacy_van_diep_profile() -> Dict[str, Any]:
    return normalize_channel_profile(_LEGACY_VAN_DIEP_PROFILE, profile_id="van_diep_studio")


def get_channel_profile(
    profile_id: str,
    channel_name: str = "",
    profile_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # A job stores a snapshot of its selected user profile. This makes retries
    # reproducible even when the user later edits/deletes the saved profile.
    if profile_data:
        profile = normalize_channel_profile(profile_data, profile_id=profile_id or "custom")
    elif profile_id == "van_diep_studio":
        profile = legacy_van_diep_profile()  # legacy jobs only
    else:
        profile = generic_channel_profile()
    if channel_name:
        profile["channel_name"] = " ".join(str(channel_name).split())[:100]
    return profile
