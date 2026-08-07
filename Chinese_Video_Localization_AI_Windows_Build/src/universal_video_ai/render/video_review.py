"""Actionable quality review for finished videos and their subtitles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .prepublish import PrepublishReport, inspect_for_publish, prepublish_report_to_dict
from .quality_check import analyze_output_quality

__all__ = [
    "VideoReviewFinding",
    "VideoReviewReport",
    "review_finished_video",
    "video_review_report_to_dict",
]

_MAX_SHORTS_SECONDS = 60.0
_MAX_REELS_SECONDS = 90.0
_MAX_SUBTITLE_CHARS = 72
_MAX_SUBTITLE_CHARS_PER_SECOND = 18.0
_MIN_SEGMENT_SECONDS = 0.35


@dataclass(frozen=True)
class VideoReviewFinding:
    """One actionable improvement finding."""

    severity: str
    category: str
    message: str
    action: str


@dataclass(frozen=True)
class VideoReviewReport:
    """Combined technical, subtitle, and publishing-readiness review."""

    score: int
    verdict: str
    summary: tuple[str, ...]
    findings: tuple[VideoReviewFinding, ...]
    next_actions: tuple[str, ...]
    prepublish: dict[str, Any]
    qc_warnings: tuple[str, ...]


def review_finished_video(
    video_path: Path,
    *,
    title: str = "",
    source_url: str = "",
    target_language: str = "",
    segments: Optional[Iterable[dict[str, Any]]] = None,
    source_duration: Optional[float] = None,
) -> VideoReviewReport:
    """Inspect a rendered video and return practical improvement advice."""
    prepublish = inspect_for_publish(video_path)
    qc_warnings = tuple(analyze_output_quality(video_path, source_duration=source_duration))
    findings: list[VideoReviewFinding] = []
    findings.extend(_findings_from_prepublish(prepublish))
    findings.extend(_findings_from_qc(qc_warnings))
    findings.extend(_findings_from_segments(tuple(segments or ())))
    findings.extend(_findings_from_context(prepublish, title=title, source_url=source_url, target_language=target_language))

    score = _score(findings)
    verdict = _verdict(score, findings)
    return VideoReviewReport(
        score=score,
        verdict=verdict,
        summary=tuple(_summary(prepublish, title=title, target_language=target_language)),
        findings=tuple(findings),
        next_actions=tuple(_next_actions(findings)),
        prepublish=prepublish_report_to_dict(prepublish),
        qc_warnings=qc_warnings,
    )


def video_review_report_to_dict(report: VideoReviewReport) -> dict[str, Any]:
    """Convert report to a stable JSON-safe shape for the web UI."""
    return {
        "score": report.score,
        "verdict": report.verdict,
        "summary": list(report.summary),
        "findings": [finding.__dict__ for finding in report.findings],
        "next_actions": list(report.next_actions),
        "prepublish": report.prepublish,
        "qc_warnings": list(report.qc_warnings),
    }


def _findings_from_prepublish(report: PrepublishReport) -> list[VideoReviewFinding]:
    findings: list[VideoReviewFinding] = []
    for issue in report.issues:
        findings.append(VideoReviewFinding(
            severity="error" if issue.severity == "error" else "warning",
            category="technical",
            message=issue.message,
            action=_action_for_issue(issue.code),
        ))
    facts = report.facts
    if facts.duration > _MAX_REELS_SECONDS:
        findings.append(VideoReviewFinding(
            "warning",
            "platform",
            f"Video dai {facts.duration:.0f}s; qua dai cho nhieu luong Shorts/Reels nhanh.",
            "Can nhac cat thanh 2 phan hoac tao ban 45-60s cho short-form.",
        ))
    elif facts.duration > _MAX_SHORTS_SECONDS:
        findings.append(VideoReviewFinding(
            "info",
            "platform",
            f"Video dai {facts.duration:.0f}s; phu hop Reels nhung co the vuot ky vong Shorts nhanh.",
            "Neu dang Shorts/TikTok, tao them ban tom tat 30-45s.",
        ))
    return findings


def _findings_from_qc(warnings: tuple[str, ...]) -> list[VideoReviewFinding]:
    return [
        VideoReviewFinding(
            "warning",
            "audio",
            warning,
            "Nghe lai 10 giay dau va 10 giay giua video; neu nho, render lai voi audio gain cao hon.",
        )
        for warning in warnings
    ]


def _findings_from_segments(segments: tuple[dict[str, Any], ...]) -> list[VideoReviewFinding]:
    findings: list[VideoReviewFinding] = []
    if not segments:
        findings.append(VideoReviewFinding(
            "info",
            "subtitle",
            "Khong co subtitle/segments de kiem tra do dai cau.",
            "Neu video co voice, nen luu segments de app bat duoc subtitle qua dai hoac qua nhanh.",
        ))
        return findings

    long_lines = 0
    fast_lines = 0
    tiny_segments = 0
    weak_hook = False
    for index, segment in enumerate(segments):
        text = str(segment.get("text") or "").strip()
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        duration = max(end - start, 0.0)
        if len(text) > _MAX_SUBTITLE_CHARS:
            long_lines += 1
        if duration > 0 and len(text) / duration > _MAX_SUBTITLE_CHARS_PER_SECOND:
            fast_lines += 1
        if 0 < duration < _MIN_SEGMENT_SECONDS:
            tiny_segments += 1
        if index == 0 and _hook_is_weak(text):
            weak_hook = True

    if weak_hook:
        findings.append(VideoReviewFinding(
            "warning",
            "hook",
            "Cau dau tien chua tao duoc hook ro trong 3 giay dau.",
            "Viet lai cau mo dau thanh loi hua cu the, van de dau dau, hoac ket qua nguoi xem se dat duoc.",
        ))
    if long_lines:
        findings.append(VideoReviewFinding(
            "warning",
            "subtitle",
            f"{long_lines} subtitle qua dai, de bi tran hoac doc khong kip tren mobile.",
            "Cat moi cau thanh 1-2 dong ngan; uu tien duoi 60-70 ky tu moi subtitle.",
        ))
    if fast_lines:
        findings.append(VideoReviewFinding(
            "warning",
            "subtitle",
            f"{fast_lines} subtitle co toc do doc qua nhanh.",
            "Them pause hoac tach cau de nguoi xem kip doc.",
        ))
    if tiny_segments:
        findings.append(VideoReviewFinding(
            "info",
            "timing",
            f"{tiny_segments} segment qua ngan de doc thoai mai.",
            "Gop cac segment rat ngan voi cau truoc/sau neu noi dung lien mach.",
        ))
    return findings


def _findings_from_context(report: PrepublishReport, *, title: str, source_url: str, target_language: str) -> list[VideoReviewFinding]:
    findings: list[VideoReviewFinding] = []
    if not title or len(title.strip()) < 8:
        findings.append(VideoReviewFinding(
            "info",
            "metadata",
            "Job chua co title ro de dang len nen tang.",
            "Tao 3-5 title ngan, co keyword chinh va loi ich ro rang truoc khi publish.",
        ))
    if source_url.startswith("creator:") and target_language:
        findings.append(VideoReviewFinding(
            "info",
            "content",
            "Video AI nen duoc kiem tra bang mat ve do khop giua voice va canh.",
            "Xem lai tung doan 5-8 giay; neu canh nen chung chung, bo sung visual brief cu the hon va render lai.",
        ))
    if not any(platform["status"] == "ready" for platform in prepublish_report_to_dict(report)["platforms"]):
        findings.append(VideoReviewFinding(
            "warning",
            "platform",
            "Khung hinh chua khop tot voi profile publish pho bien.",
            "Chon lai aspect ratio 9:16 cho Shorts/Reels/TikTok hoac 16:9 cho YouTube ngang.",
        ))
    return findings


def _hook_is_weak(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    weak_starts = ("hello", "hi ", "xin chao", "xin chào", "trong video này", "hom nay", "hôm nay")
    return len(text) > 140 or lowered.startswith(weak_starts)


def _action_for_issue(code: str) -> str:
    return {
        "invalid_dimensions": "Render lai voi template/aspect ratio hop le.",
        "missing_audio": "Kiem tra buoc TTS/mix audio, sau do render lai.",
        "invalid_duration": "Render lai va kiem tra ffmpeg log.",
        "low_resolution": "Dung preset chat luong cao hon hoac source media do phan giai cao hon.",
        "unusual_fps": "Render lai voi FPS 24, 30 hoac 60.",
        "uncommon_video_codec": "Export lai bang H.264 de tuong thich tot hon.",
        "uncommon_pixel_format": "Export lai pixel format yuv420p.",
    }.get(code, "Xem lai cau hinh render va tao ban export moi neu can.")


def _score(findings: tuple[VideoReviewFinding, ...] | list[VideoReviewFinding]) -> int:
    score = 100
    for finding in findings:
        if finding.severity == "error":
            score -= 28
        elif finding.severity == "warning":
            score -= 12
        else:
            score -= 4
    return max(0, min(100, score))


def _verdict(score: int, findings: tuple[VideoReviewFinding, ...] | list[VideoReviewFinding]) -> str:
    if any(finding.severity == "error" for finding in findings):
        return "Can sua loi ky thuat truoc khi publish."
    if score >= 85:
        return "San sang publish sau khi xem lai bang mat."
    if score >= 65:
        return "Co the publish, nhung nen sua cac diem warning truoc."
    return "Nen cai thien va render lai truoc khi publish."


def _summary(report: PrepublishReport, *, title: str, target_language: str) -> list[str]:
    facts = report.facts
    return [
        f"{facts.width}x{facts.height}, {facts.duration:.1f}s, {facts.fps:.1f} FPS",
        f"Video codec {facts.video_codec}, audio {'co' if facts.has_audio else 'khong co'}",
        f"Title: {title or 'chua co'}",
        f"Target language: {target_language or 'unknown'}",
    ]


def _next_actions(findings: tuple[VideoReviewFinding, ...] | list[VideoReviewFinding]) -> list[str]:
    if not findings:
        return ["Xem lai video bang mat lan cuoi, sau do publish hoac schedule."]
    actions = []
    for finding in findings:
        if finding.action not in actions:
            actions.append(finding.action)
        if len(actions) >= 5:
            break
    return actions
