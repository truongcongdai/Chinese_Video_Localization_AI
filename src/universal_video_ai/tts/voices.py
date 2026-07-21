# src/universal_video_ai/tts/voices.py
"""
Curated male/female Edge-TTS voice choices per language, for the "chọn
giọng đọc" dropdown in the web UI — as opposed to
`tts.DEFAULT_VOICES_BY_LANGUAGE`, which is just ONE hardcoded voice per
language used when nobody picks one explicitly.

Only languages with a genuinely good Edge neural voice pair are listed;
for anything else the UI falls back to "Mặc định" (None), which resolves
to `tts.voice_for_language()` same as before this feature existed.
"""
from __future__ import annotations

from typing import Dict, List, TypedDict

__all__ = ["VoiceOption", "VOICE_OPTIONS", "voices_for_language"]


class VoiceOption(TypedDict):
    id: str        # Edge TTS voice name, e.g. "vi-VN-HoaiMyNeural"
    label: str      # shown in the dropdown, e.g. "Nữ - Hoài My"


VOICE_OPTIONS: Dict[str, List[VoiceOption]] = {
    "vi": [
        {"id": "vi-VN-HoaiMyNeural", "label": "Nữ - Hoài My"},
        {"id": "vi-VN-HoaiMyNeural|rate=+12%", "label": "Nữ - Hoài My năng động"},
        {"id": "vi-VN-HoaiMyNeural|rate=-10%|pitch=-2Hz", "label": "Nữ - Hoài My trầm, chậm"},
        {"id": "vi-VN-NamMinhNeural", "label": "Nam - Nam Minh"},
        {"id": "vi-VN-NamMinhNeural|rate=+12%|pitch=+2Hz", "label": "Nam - Nam Minh trẻ trung"},
        {"id": "vi-VN-NamMinhNeural|rate=-10%|pitch=-3Hz", "label": "Nam - Nam Minh trầm ấm"},
    ],
    "en": [
        {"id": "en-US-JennyNeural", "label": "Nữ - Jenny (US)"},
        {"id": "en-US-GuyNeural", "label": "Nam - Guy (US)"},
        {"id": "en-US-AriaNeural", "label": "Nữ - Aria (US)"},
        {"id": "en-US-DavisNeural", "label": "Nam - Davis (US)"},
        {"id": "en-GB-SoniaNeural", "label": "Nữ - Sonia (UK)"},
        {"id": "en-GB-RyanNeural", "label": "Nam - Ryan (UK)"},
    ],
    "zh": [
        {"id": "zh-CN-XiaoxiaoNeural", "label": "Nữ - Xiaoxiao"},
        {"id": "zh-CN-YunxiNeural", "label": "Nam - Yunxi"},
    ],
    "zh-tw": [
        {"id": "zh-TW-HsiaoChenNeural", "label": "Nữ - Hsiao Chen (Đài Loan)"},
        {"id": "zh-TW-YunJheNeural", "label": "Nam - Yun Jhe (Đài Loan)"},
    ],
    "ja": [
        {"id": "ja-JP-NanamiNeural", "label": "Nữ - Nanami"},
        {"id": "ja-JP-KeitaNeural", "label": "Nam - Keita"},
    ],
    "ko": [
        {"id": "ko-KR-SunHiNeural", "label": "Nữ - SunHi"},
        {"id": "ko-KR-InJoonNeural", "label": "Nam - InJoon"},
    ],
    "fr": [
        {"id": "fr-FR-DeniseNeural", "label": "Nữ - Denise"},
        {"id": "fr-FR-HenriNeural", "label": "Nam - Henri"},
    ],
    "de": [
        {"id": "de-DE-KatjaNeural", "label": "Nữ - Katja"},
        {"id": "de-DE-ConradNeural", "label": "Nam - Conrad"},
    ],
    "es": [
        {"id": "es-ES-ElviraNeural", "label": "Nữ - Elvira"},
        {"id": "es-ES-AlvaroNeural", "label": "Nam - Alvaro"},
    ],
    "pt": [
        {"id": "pt-BR-FranciscaNeural", "label": "Nữ - Francisca"},
        {"id": "pt-BR-AntonioNeural", "label": "Nam - Antonio"},
    ],
    "ru": [
        {"id": "ru-RU-SvetlanaNeural", "label": "Nữ - Svetlana"},
        {"id": "ru-RU-DmitryNeural", "label": "Nam - Dmitry"},
    ],
    "th": [
        {"id": "th-TH-PremwadeeNeural", "label": "Nữ - Premwadee"},
        {"id": "th-TH-NiwatNeural", "label": "Nam - Niwat"},
    ],
    "id": [
        {"id": "id-ID-GadisNeural", "label": "Nữ - Gadis"},
        {"id": "id-ID-ArdiNeural", "label": "Nam - Ardi"},
    ],
    "ar": [
        {"id": "ar-SA-ZariyahNeural", "label": "Nữ - Zariyah"},
        {"id": "ar-SA-HamedNeural", "label": "Nam - Hamed"},
    ],
    "hi": [
        {"id": "hi-IN-SwaraNeural", "label": "Nữ - Swara"},
        {"id": "hi-IN-MadhurNeural", "label": "Nam - Madhur"},
    ],
}


def voices_for_language(language: str) -> List[VoiceOption]:
    key = (language or "").strip().lower()
    base = VOICE_OPTIONS.get(key)
    if base is None:
        base = VOICE_OPTIONS.get(key.split("-")[0], [])
    if key.split("-")[0] == "vi":
        return list(base)

    # Every non-Vietnamese base voice also gets expressive pacing choices.
    # Edge TTS supports rate/pitch independently of locale, so this expands
    # all languages consistently without maintaining hundreds of near-
    # duplicate hardcoded entries.
    expanded: List[VoiceOption] = []
    for voice in base:
        expanded.extend([
            voice,
            {"id": f'{voice["id"]}|rate=+12%|pitch=+2Hz', "label": f'{voice["label"]} · năng động'},
            {"id": f'{voice["id"]}|rate=-10%|pitch=-2Hz', "label": f'{voice["label"]} · trầm, chậm'},
        ])
    return expanded
