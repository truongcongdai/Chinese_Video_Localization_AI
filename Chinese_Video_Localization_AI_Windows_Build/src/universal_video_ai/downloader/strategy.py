from __future__ import annotations

import logging

from .platform import Platform

logger = logging.getLogger(__name__)


class DownloadStrategy:
    """Small helper to choose a download strategy based on platform.

    This module intentionally uses logging (no prints) so library consumers can
    control output via logging configuration.
    """

    @staticmethod
    def get(platform: Platform) -> Platform:
        """Return the provided platform (placeholder for future strategy selection).

        Keeping this small and explicit avoids duplicating logic with the factory.
        """
        logger.debug("Using strategy for %s", platform.value)
        return platform