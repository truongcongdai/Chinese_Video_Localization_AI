from __future__ import annotations

from .normalization import clamp


def weighted_score(weighted_components: list[tuple[float, float]]) -> float:
    total_weight = sum(weight for weight, _score in weighted_components if weight > 0)
    if total_weight <= 0:
        return 0.0
    value = sum(weight * clamp(score) for weight, score in weighted_components if weight > 0)
    return clamp(value / total_weight)


def confidence_from_sample_size(sample_size: int, full_confidence_at: int = 30) -> float:
    if full_confidence_at <= 0:
        return 100.0
    return clamp((max(0, sample_size) / full_confidence_at) * 100.0)


def confidence_factor(sample_size: int, full_confidence_at: int = 30) -> float:
    return confidence_from_sample_size(sample_size, full_confidence_at) / 100.0
