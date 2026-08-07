from __future__ import annotations

from .normalization import clamp
from .schemas import CompetitionAnalysis, OpportunityAnalysis, TrendAnalysis
from .scoring import confidence_factor, weighted_score


class OpportunityAnalyzer:
    def analyze(
        self,
        trend: TrendAnalysis,
        competition: CompetitionAnalysis,
        content_gap_score: float = 50.0,
        evergreen_score: float = 50.0,
        monetization_potential_score: float = 50.0,
    ) -> OpportunityAnalysis:
        content_gap_score = clamp(content_gap_score)
        evergreen_score = clamp(evergreen_score)
        monetization_potential_score = clamp(monetization_potential_score)
        raw_score = weighted_score([
            (0.30, trend.trend_score),
            (0.25, 100.0 - competition.competition_score),
            (0.20, content_gap_score),
            (0.15, evergreen_score),
            (0.10, monetization_potential_score),
        ])
        confidence_score = min(trend.confidence_score, competition.confidence_score)
        adjusted_score = clamp(raw_score * confidence_factor(int(confidence_score), full_confidence_at=100))

        positive_signals: list[str] = []
        negative_signals: list[str] = []
        risks: list[str] = []
        if trend.trend_score >= 65:
            positive_signals.append("Strong trend score in the current sample.")
        if competition.small_channel_breakout_score >= 10:
            positive_signals.append("Small channels show breakout potential.")
        if competition.competition_score >= 70:
            negative_signals.append("Competition is strong for this query.")
        if competition.title_saturation_score >= 60:
            risks.append("Many titles look similar; avoid cloning competitor framing.")
        if confidence_score < 50:
            risks.append("Small sample size; treat the score as directional, not predictive.")

        explanations = [
            "Opportunity score combines trend, inverse competition, content gap, evergreen value, and monetization potential.",
            f"Adjusted score applies sample confidence ({confidence_score:.1f}/100).",
        ]
        return OpportunityAnalysis(
            raw_score=raw_score,
            adjusted_score=adjusted_score,
            confidence_score=confidence_score,
            trend_score=trend.trend_score,
            competition_score=competition.competition_score,
            content_gap_score=content_gap_score,
            evergreen_score=evergreen_score,
            monetization_potential_score=monetization_potential_score,
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            risks=risks,
            explanations=explanations,
            suggested_angles=[
                "Beginner-focused practical guide",
                "Comparison with clear decision criteria",
                "Updated workflow using current tools",
            ],
            suggested_formats=["Long-form tutorial", "Shorts summary", "Case study"],
            metadata={"score_is_prediction": False},
        )
