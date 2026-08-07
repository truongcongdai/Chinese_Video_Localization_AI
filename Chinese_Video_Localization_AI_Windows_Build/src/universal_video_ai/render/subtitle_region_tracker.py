from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Iterable, List, Optional, Protocol, Sequence, Tuple, TypeVar

__all__ = [
    "AdaptiveSubtitleRegionConfig",
    "AdaptiveSubtitleRegionTracker",
    "TrackedRegion",
]


class OverlayLike(Protocol):
    start: float
    end: float
    x: int
    y: int
    width: int
    height: int


TOverlay = TypeVar("TOverlay", bound=OverlayLike)


@dataclass(frozen=True)
class AdaptiveSubtitleRegionConfig:
    """Scale-independent subtitle-region stabilization settings.

    All spatial thresholds are fractions or ratios derived from the detected
    subtitle boxes and source-frame dimensions. There is deliberately no fixed
    subtitle Y position or resolution-specific pixel coordinate.
    """

    enabled: bool = True
    # Minimum vertical clustering tolerance as a fraction of frame height.
    cluster_tolerance_frame_ratio: float = 0.025
    # Additional tolerance derived from the median detected line height.
    cluster_tolerance_height_ratio: float = 1.35
    # A cluster with only one cue is accepted only when no stronger cluster is
    # available. This removes isolated title/watermark OCR boxes while still
    # allowing short videos with only one subtitle cue.
    minimum_cluster_size: int = 2
    # Nearby cues in the same learned cluster contribute to temporal median
    # smoothing. This is time-relative, not position-specific.
    temporal_neighbor_seconds: float = 8.0
    # Do not move a cue farther than this many detected line-heights. Large
    # movement usually means a scene really uses a different subtitle band.
    maximum_correction_height_ratio: float = 1.75
    # Adaptive mask padding, derived from detected glyph-line height.
    padding_x_height_ratio: float = 0.55
    padding_y_height_ratio: float = 0.38
    # Clamp padding relative to frame size to prevent oversized masks.
    max_padding_x_frame_ratio: float = 0.035
    max_padding_y_frame_ratio: float = 0.025
    # Extra cleanup expansion for delogo/inpaint before the cover box.
    cleanup_extra_height_ratio: float = 0.42
    # A single OCR cue can occasionally merge subtitle text with a nearby
    # label, producing an abnormally tall/shifted box.  Reject such one-off
    # geometry when the surrounding cues form a stable local consensus.
    local_consensus_seconds: float = 7.0
    local_consensus_min_neighbors: int = 2
    local_center_outlier_height_ratio: float = 1.65
    local_height_outlier_ratio: float = 1.75


@dataclass(frozen=True)
class TrackedRegion:
    x: int
    y: int
    width: int
    height: int
    cleanup_x: int
    cleanup_y: int
    cleanup_width: int
    cleanup_height: int
    cluster_id: int
    confidence: float


@dataclass
class _Cluster:
    member_indexes: List[int]
    centers_y: List[float]
    heights: List[int]

    @property
    def center_y(self) -> float:
        return float(median(self.centers_y))

    @property
    def height(self) -> float:
        return float(median(self.heights))


class AdaptiveSubtitleRegionTracker:
    """Learn and stabilize subtitle regions from detected cue boxes.

    The tracker supports multiple subtitle positions in one video. It clusters
    normalized vertical centers, keeps every statistically meaningful cluster,
    rejects isolated OCR noise, and smooths each cue only against temporal
    neighbors from its own cluster.
    """

    def __init__(self, config: Optional[AdaptiveSubtitleRegionConfig] = None) -> None:
        self.config = config or AdaptiveSubtitleRegionConfig()

    @staticmethod
    def _valid(overlay: OverlayLike) -> bool:
        return (
            overlay.end > overlay.start
            and overlay.width > 1
            and overlay.height > 1
            and overlay.x >= 0
            and overlay.y >= 0
        )

    def _cluster(self, overlays: Sequence[OverlayLike], frame_h: int) -> Tuple[List[_Cluster], List[int]]:
        valid_heights = [o.height for o in overlays if self._valid(o)]
        typical_height = float(median(valid_heights)) if valid_heights else max(1.0, frame_h * 0.04)
        tolerance = max(
            frame_h * self.config.cluster_tolerance_frame_ratio,
            typical_height * self.config.cluster_tolerance_height_ratio,
        )

        order = sorted(
            range(len(overlays)),
            key=lambda idx: overlays[idx].y + overlays[idx].height / 2.0,
        )
        clusters: List[_Cluster] = []
        assignments = [-1] * len(overlays)

        for idx in order:
            overlay = overlays[idx]
            if not self._valid(overlay):
                continue
            cy = overlay.y + overlay.height / 2.0
            best_cluster: Optional[int] = None
            best_distance: Optional[float] = None
            for cluster_idx, cluster in enumerate(clusters):
                distance = abs(cy - cluster.center_y)
                if distance <= tolerance and (best_distance is None or distance < best_distance):
                    best_cluster = cluster_idx
                    best_distance = distance
            if best_cluster is None:
                clusters.append(_Cluster([idx], [cy], [overlay.height]))
                assignments[idx] = len(clusters) - 1
            else:
                cluster = clusters[best_cluster]
                cluster.member_indexes.append(idx)
                cluster.centers_y.append(cy)
                cluster.heights.append(overlay.height)
                assignments[idx] = best_cluster

        return clusters, assignments

    def _accepted_clusters(self, clusters: Sequence[_Cluster]) -> set[int]:
        if not clusters:
            return set()
        strongest = max(len(cluster.member_indexes) for cluster in clusters)
        accepted = {
            idx
            for idx, cluster in enumerate(clusters)
            if len(cluster.member_indexes) >= self.config.minimum_cluster_size
        }
        # Preserve a one-cue video or a genuinely sparse alternate location.
        if not accepted:
            accepted = {
                idx for idx, cluster in enumerate(clusters)
                if len(cluster.member_indexes) == strongest
            }
        return accepted

    @staticmethod
    def _clamp_box(x: int, y: int, w: int, h: int, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        x = max(0, min(x, max(0, frame_w - 1)))
        y = max(0, min(y, max(0, frame_h - 1)))
        w = max(1, min(w, frame_w - x))
        h = max(1, min(h, frame_h - y))
        return x, y, w, h

    def track(self, overlays: Sequence[TOverlay], frame_w: int, frame_h: int) -> List[Tuple[TOverlay, TrackedRegion]]:
        if not overlays:
            return []
        if frame_w <= 0 or frame_h <= 0 or not self.config.enabled:
            return [
                (
                    overlay,
                    TrackedRegion(
                        overlay.x,
                        overlay.y,
                        overlay.width,
                        overlay.height,
                        overlay.x,
                        overlay.y,
                        overlay.width,
                        overlay.height,
                        -1,
                        1.0,
                    ),
                )
                for overlay in overlays
            ]

        clusters, assignments = self._cluster(overlays, frame_h)
        accepted = self._accepted_clusters(clusters)
        results: List[Tuple[TOverlay, TrackedRegion]] = []

        all_valid_heights = [o.height for o in overlays if self._valid(o)]
        global_typical_height = float(median(all_valid_heights)) if all_valid_heights else max(1.0, frame_h * 0.04)

        for idx, overlay in enumerate(overlays):
            cluster_id = assignments[idx]
            source_cy = overlay.y + overlay.height / 2.0
            source_height = max(1, overlay.height)

            # Build a time-local consensus independent of the global cluster.
            # This catches a transient OCR merge without preventing a genuine
            # subtitle-position change that persists for multiple cues.
            midpoint = (overlay.start + overlay.end) / 2.0
            local_indexes = [
                other_idx
                for other_idx, other in enumerate(overlays)
                if other_idx != idx
                and self._valid(other)
                and abs(((other.start + other.end) / 2.0) - midpoint) <= self.config.local_consensus_seconds
            ]
            local_consensus_cy = None
            local_consensus_h = None
            if len(local_indexes) >= self.config.local_consensus_min_neighbors:
                local_centers = [overlays[n].y + overlays[n].height / 2.0 for n in local_indexes]
                local_heights = [overlays[n].height for n in local_indexes]
                candidate_cy = float(median(local_centers))
                candidate_h = float(median(local_heights))
                center_spread = float(median([abs(v - candidate_cy) for v in local_centers]))
                stable_local_band = center_spread <= max(global_typical_height * 0.75, candidate_h * 0.75)
                center_outlier = abs(source_cy - candidate_cy) > max(
                    global_typical_height * self.config.local_center_outlier_height_ratio,
                    candidate_h * self.config.local_center_outlier_height_ratio,
                )
                height_outlier = source_height > max(
                    global_typical_height * self.config.local_height_outlier_ratio,
                    candidate_h * self.config.local_height_outlier_ratio,
                )
                if stable_local_band and (center_outlier or height_outlier):
                    local_consensus_cy = candidate_cy
                    local_consensus_h = candidate_h
            confidence = 1.0
            smoothed_cy = source_cy
            smoothed_height = float(source_height)

            if local_consensus_cy is not None and local_consensus_h is not None:
                smoothed_cy = local_consensus_cy
                smoothed_height = local_consensus_h
                confidence = 0.62
            elif cluster_id >= 0 and cluster_id in accepted:
                cluster = clusters[cluster_id]
                midpoint = (overlay.start + overlay.end) / 2.0
                neighbor_indexes = [
                    member_idx
                    for member_idx in cluster.member_indexes
                    if abs(
                        ((overlays[member_idx].start + overlays[member_idx].end) / 2.0) - midpoint
                    ) <= self.config.temporal_neighbor_seconds
                ]
                if not neighbor_indexes:
                    neighbor_indexes = cluster.member_indexes
                neighbor_centers = [
                    overlays[n].y + overlays[n].height / 2.0 for n in neighbor_indexes
                ]
                neighbor_heights = [overlays[n].height for n in neighbor_indexes]
                candidate_cy = float(median(neighbor_centers))
                candidate_height = float(median(neighbor_heights))
                max_shift = max(source_height, candidate_height) * self.config.maximum_correction_height_ratio
                if abs(candidate_cy - source_cy) <= max_shift:
                    smoothed_cy = candidate_cy
                    smoothed_height = candidate_height
                    confidence = min(1.0, 0.55 + 0.1 * len(neighbor_indexes))
                else:
                    # The detected cue is too far away to be safely pulled to
                    # this band. Preserve its own position so alternate subtitle
                    # layouts remain supported.
                    confidence = 0.45
            elif cluster_id >= 0:
                # Isolated cluster: likely title/watermark. Preserve the raw box
                # only if there is no accepted cluster nearby in time; otherwise
                # reuse the nearest accepted local subtitle band.
                midpoint = (overlay.start + overlay.end) / 2.0
                candidates: List[Tuple[float, int]] = []
                for accepted_id in accepted:
                    for member_idx in clusters[accepted_id].member_indexes:
                        other = overlays[member_idx]
                        other_mid = (other.start + other.end) / 2.0
                        candidates.append((abs(other_mid - midpoint), member_idx))
                if candidates:
                    distance, nearest_idx = min(candidates)
                    nearest = overlays[nearest_idx]
                    if distance <= self.config.temporal_neighbor_seconds:
                        smoothed_cy = nearest.y + nearest.height / 2.0
                        smoothed_height = float(nearest.height)
                        confidence = 0.5
                    else:
                        confidence = 0.25

            base_height = max(2, int(round(smoothed_height)))
            base_y = int(round(smoothed_cy - base_height / 2.0))
            base_x = overlay.x
            base_width = overlay.width

            pad_x = min(
                int(round(base_height * self.config.padding_x_height_ratio)),
                int(round(frame_w * self.config.max_padding_x_frame_ratio)),
            )
            pad_y = min(
                int(round(base_height * self.config.padding_y_height_ratio)),
                int(round(frame_h * self.config.max_padding_y_frame_ratio)),
            )
            pad_x = max(1, pad_x)
            pad_y = max(1, pad_y)

            x, y, width, height = self._clamp_box(
                base_x - pad_x,
                base_y - pad_y,
                base_width + 2 * pad_x,
                base_height + 2 * pad_y,
                frame_w,
                frame_h,
            )

            cleanup_extra = max(1, int(round(base_height * self.config.cleanup_extra_height_ratio)))
            cleanup_x, cleanup_y, cleanup_width, cleanup_height = self._clamp_box(
                x - cleanup_extra,
                y - cleanup_extra,
                width + 2 * cleanup_extra,
                height + 2 * cleanup_extra,
                frame_w,
                frame_h,
            )

            results.append(
                (
                    overlay,
                    TrackedRegion(
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        cleanup_x=cleanup_x,
                        cleanup_y=cleanup_y,
                        cleanup_width=cleanup_width,
                        cleanup_height=cleanup_height,
                        cluster_id=cluster_id,
                        confidence=confidence,
                    ),
                )
            )

        return results