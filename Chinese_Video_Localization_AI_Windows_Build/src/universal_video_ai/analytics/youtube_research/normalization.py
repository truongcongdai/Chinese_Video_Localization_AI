from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Iterable


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "the", "to", "vs", "with", "you",
    "your", "cach", "cua", "cho", "la", "mot", "nhung", "the", "va", "voi",
}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if value is None or not math.isfinite(float(value)):
        return low
    return max(low, min(high, float(value)))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator in (0, 0.0) or not math.isfinite(float(denominator)):
        return default
    value = float(numerator) / float(denominator)
    return value if math.isfinite(value) else default


def median(values: Iterable[float], default: float = 0.0) -> float:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return default
    return float(statistics.median(clean))


def percentile(values: Iterable[float], percent: float, default: float = 0.0) -> float:
    clean = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not clean:
        return default
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * clamp(percent, 0.0, 100.0) / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return clean[lower]
    weight = rank - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def winsorized(values: Iterable[float], low_percent: float = 5.0, high_percent: float = 95.0) -> list[float]:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return []
    low = percentile(clean, low_percent)
    high = percentile(clean, high_percent)
    return [max(low, min(high, v)) for v in clean]


def log_score(value: float, reference: float) -> float:
    if value <= 0 or reference <= 0:
        return 0.0
    return clamp(100.0 * math.log1p(value) / math.log1p(reference))


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    return [
        token for token in _TOKEN_RE.findall(normalized)
        if len(token) > 1 and token not in _STOP_WORDS
    ]


def jaccard_similarity(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return safe_divide(len(left_tokens & right_tokens), len(left_tokens | right_tokens))
