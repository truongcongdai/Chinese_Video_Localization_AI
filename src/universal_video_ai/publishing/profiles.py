from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


_VAN_DIEP_PROFILE: Dict[str, Any] = {
    "id": "van_diep_studio",
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
    "title_formula": "Mâu thuẫn lớn + kết quả hoặc hệ thống đặc biệt + keyword ngách",
    "thumbnail_rules": {
        "max_words": 5,
        "examples": [
            "LÃO TỔ TRỞ VỀ", "GIA TỘC QUẬT KHỞI", "VẠN NĂM TRƯỜNG SINH",
            "MỘT NGƯỜI NUÔI CẢ TỘC", "TỪ PHÀM TỘC ĐẾN TIÊN TỘC",
        ],
    },
    "description_template": (
        "📖 Tên truyện: {story_name}\n\n"
        "Trong video này, Vạn Diệp Studio sẽ kể lại hành trình {episode_journey}.\n\n"
        "{episode_summary}\n\n"
        "Đây là series review truyện tu tiên, trường sinh, xây dựng gia tộc, "
        "phát triển tông môn và thế lực.\n\n"
        "▶ Xem trọn bộ series: {playlist_url}\n"
        "▶ Đăng ký Vạn Diệp Studio để theo dõi tập tiếp theo.\n\n"
        "Nội dung chính:\n"
        "• Review truyện tu tiên và tiên hiệp\n"
        "• Tu tiên gia tộc, gia tộc trường sinh\n"
        "• Xây dựng tông môn và phát triển thế lực\n"
        "• Lão tổ, hệ thống gia tộc và tiên tộc quật khởi\n\n"
        "{hashtags}"
    ),
}

_GENERIC_PROFILE: Dict[str, Any] = {
    "id": "generic_reup",
    "channel_name": "Kênh của tôi",
    "language": "vi",
    "niche": "Video reup đã biên tập và bản địa hóa",
    "brand_line": "",
    "audience": "Khán giả Việt Nam",
    "category": "Entertainment",
    "made_for_kids": False,
    "default_privacy": "private",
    "primary_keyword_groups": {"format": ["video thuyết minh", "video tiếng Việt"]},
    "base_tags": [],
    "base_hashtags": [],
    "forbidden_generic_keywords": [],
    "title_formula": "Mâu thuẫn hoặc lợi ích chính + chủ đề cụ thể",
    "thumbnail_rules": {"max_words": 5, "examples": []},
    "description_template": "{episode_summary}\n\n{hashtags}",
}


def get_channel_profile(profile_id: str, channel_name: str = "") -> Dict[str, Any]:
    profile = deepcopy(_VAN_DIEP_PROFILE if profile_id == "van_diep_studio" else _GENERIC_PROFILE)
    if channel_name:
        profile["channel_name"] = channel_name
    return profile
