# src/universal_video_ai/render/ocr_language_map.py
"""
Maps a spoken-audio language code (as detected by Whisper, e.g. "zh", "ja",
"vi") to the matching `easyocr` language pack(s) to use when scanning for
burned-in on-screen text — so this app isn't limited to assuming every
source video's hardcoded subtitle is Chinese.

Why this can't just be "pass the detected language straight to easyocr":
easyocr only allows combining languages from within the same script-
compatible group (e.g. its "Chinese-compatible" group of `ch_sim`/`ch_tra`
can only be paired with `en`; it can't be combined with `ja` or `ko` in one
reader). So each entry here is a small, easyocr-valid language list —
almost always [<script for this language>, "en"], since on-screen text
often mixes the source script with Latin characters/numbers.
"""
from __future__ import annotations

from typing import Optional, Tuple

__all__ = ["OCR_LANGUAGE_MAP", "AUTO_OCR_SENTINEL", "resolve_ocr_languages"]

# Sentinel LocalizationConfig.ocr_languages / factory ocr_languages can be
# set to, meaning "pick automatically from the detected spoken language"
# instead of a fixed easyocr language list.
AUTO_OCR_SENTINEL: Tuple[str, ...] = ("auto",)

# Whisper language code -> easyocr language pack. Keys are the primary
# subtag (lowercase), so "zh-cn"/"zh-tw" etc. all reduce to "zh" before
# lookup here (see `resolve_ocr_languages`).
OCR_LANGUAGE_MAP = {
    "zh": ("ch_sim", "en"),
    "ja": ("ja", "en"),
    "ko": ("ko", "en"),
    "th": ("th", "en"),
    "ar": ("ar", "en"),
    "hi": ("hi", "en"),
    "vi": ("vi", "en"),
    # Latin-script languages easyocr can freely mix with English in one
    # reader (its "Latin-compatible" group).
    "en": ("en",),
    "fr": ("fr", "en"),
    "de": ("de", "en"),
    "es": ("es", "en"),
    "pt": ("pt", "en"),
    "id": ("id", "en"),
    "it": ("it", "en"),
    "nl": ("nl", "en"),
}

# Used when nothing was detected, or the detected language has no mapping
# above — this project's most common source content is Chinese short-drama
# reuploads, so this preserves the previous hardcoded default rather than
# silently disabling on-screen text detection.
_FALLBACK = ("ch_sim", "en")


def resolve_ocr_languages(
    configured: Tuple[str, ...],
    detected_language: Optional[str],
) -> Tuple[str, ...]:
    """
    :param configured: `LocalizationConfig.ocr_languages` as set by the
        caller — either an explicit easyocr language tuple (used as-is,
        unchanged), or `AUTO_OCR_SENTINEL` to resolve from `detected_language`.
    :param detected_language: language code Whisper detected the source
        audio to be in (e.g. from `AudioPipelineResult.detected_language`),
        or None if unknown/unavailable.
    :return: an easyocr-valid language tuple.
    """
    if configured != AUTO_OCR_SENTINEL:
        return configured

    if not detected_language:
        return _FALLBACK

    primary = detected_language.strip().lower().split("-")[0]
    return OCR_LANGUAGE_MAP.get(primary, _FALLBACK)
