"""Lightweight voice direction for creator narration."""

from __future__ import annotations

from dataclasses import dataclass
import re

__all__ = ["DirectedCue", "direct_voice_cue"]


@dataclass(frozen=True)
class DirectedCue:
    """One narration cue with speaking instructions for Edge TTS."""

    text: str
    rate: str
    pitch: str


def direct_voice_cue(text: str, index: int, total: int, language: str = "en") -> DirectedCue:
    """Make a cue sound less flat by adjusting punctuation, rate and pitch.

    Edge TTS does not expose full acting controls in this pipeline, but it
    does support rate/pitch per request. Since creator narration is already
    synthesized cue-by-cue, varying these settings by cue gives the voice a
    more intentional hook, explanation, and CTA cadence without time-stretching
    the final audio.
    """
    cleaned = _naturalize_text(text)
    if (language or "").lower().split("-")[0] == "vi":
        cleaned = _restore_common_vietnamese_diacritics(cleaned)
    lowered = cleaned.lower()
    is_first = index == 0
    is_last = index >= max(total - 1, 0)
    is_question = cleaned.endswith("?")
    is_cta = any(token in lowered for token in _cta_tokens(language))
    word_count = len(cleaned.split())

    rate = "+6%"
    pitch = "+1Hz"
    if is_first:
        rate = "+2%"
        pitch = "+4Hz"
    elif is_question:
        rate = "+7%"
        pitch = "+3Hz"
    elif is_last or is_cta:
        rate = "+4%"
        pitch = "+2Hz"
    elif word_count >= 18:
        rate = "+3%"
        pitch = "+0Hz"

    return DirectedCue(text=cleaned, rate=rate, pitch=pitch)


def _naturalize_text(text: str) -> str:
    cleaned = " ".join(str(text).replace(";", ".").split())
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    if not cleaned:
        return cleaned
    if cleaned[-1] not in ".!?":
        cleaned += "."
    # A short dash often gets spoken awkwardly by TTS; punctuation creates a
    # cleaner pause.
    cleaned = re.sub(r"\s+-\s+", ". ", cleaned)
    return cleaned


def _restore_common_vietnamese_diacritics(text: str) -> str:
    """Repair common ASCII Vietnamese template phrases before TTS speaks them."""
    phrase_map = {
        "Neu ban dang muon lam": "Nếu bạn đang muốn làm",
        "dung bat dau truoc khi biet diem nay": "đừng bắt đầu trước khi biết điểm này",
        "Ban co chac minh da hieu dung ve": "Bạn có chắc mình đã hiểu đúng về",
        "Dung mua voi": "Đừng mua vội",
        "neu ban chua thay phan test that nay": "nếu bạn chưa thấy phần test thật này",
        "Khoan mua voi": "Khoan mua vội",
        "Nghe minh noi that": "Nghe mình nói thật",
    }
    repaired = text
    for source, target in phrase_map.items():
        repaired = re.sub(re.escape(source), target, repaired, flags=re.IGNORECASE)
    word_map = {
        "ban": "bạn",
        "dang": "đang",
        "muon": "muốn",
        "dung": "đừng",
        "bat": "bắt",
        "dau": "đầu",
        "truoc": "trước",
        "biet": "biết",
        "diem": "điểm",
        "nay": "này",
        "hay": "hãy",
        "luu": "lưu",
        "video": "video",
        "theo": "theo",
        "doi": "dõi",
        "binh": "bình",
        "luan": "luận",
        "chia": "chia",
        "se": "sẻ",
    }

    def replace_word(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = word_map.get(original.lower())
        if not replacement:
            return original
        return replacement.capitalize() if original[:1].isupper() else replacement

    return re.sub(r"\b[a-zA-Z]+\b", replace_word, repaired)


def _cta_tokens(language: str) -> tuple[str, ...]:
    primary = (language or "").lower().split("-")[0]
    if primary == "vi":
        return ("hãy", "hay ", "đừng quên", "dang ky", "đăng ký", "binh luan", "bình luận", "chia se", "chia sẻ")
    return ("try", "start", "subscribe", "comment", "share", "save this", "remember")
