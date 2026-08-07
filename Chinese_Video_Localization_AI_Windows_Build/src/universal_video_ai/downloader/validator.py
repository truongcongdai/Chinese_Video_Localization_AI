# src/universal_video_ai/downloader/validator.py
from __future__ import annotations

from pathlib import Path
import logging
from typing import Iterable, Optional, Set
from urllib.parse import urlparse

__all__ = ["UrlValidator", "FileValidator", "validate_url_or_raise"]

_logger = logging.getLogger(__name__)


class UrlValidator:
    """
    Validate video URLs used by downloaders.

    Rules:
    - URL must have scheme 'http' or 'https'.
    - URL must include a non-empty hostname.
    """

    ALLOWED_SCHEMES = {"http", "https"}

    @classmethod
    def is_valid(cls, url: str) -> bool:
        """
        Return True if URL appears valid for download attempts.

        :param url: URL string to validate.
        """
        if not url or not isinstance(url, str):
            _logger.debug("UrlValidator.is_valid: invalid type or empty url=%r", url)
            return False

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        hostname = parsed.hostname

        if scheme not in cls.ALLOWED_SCHEMES:
            _logger.debug("UrlValidator.is_valid: unsupported scheme=%r for url=%r", scheme, url)
            return False

        if not hostname:
            _logger.debug("UrlValidator.is_valid: missing hostname for url=%r", url)
            return False

        _logger.debug("UrlValidator.is_valid: url=%r is valid", url)
        return True

    @classmethod
    def validate_or_raise(cls, url: str) -> None:
        """
        Validate the URL or raise ValueError with descriptive message.

        :param url: URL string to validate.
        :raises ValueError: if validation fails.
        """
        if not url or not isinstance(url, str):
            raise ValueError("URL must be a non-empty string")

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        hostname = parsed.hostname

        if scheme not in cls.ALLOWED_SCHEMES:
            raise ValueError(f"Unsupported URL scheme {scheme!r}; only http/https are allowed")

        if not hostname:
            raise ValueError("URL must include a hostname")


def validate_url_or_raise(url: str) -> None:
    """
    Convenience helper that validates a URL and raises ValueError on failure.

    :param url: URL string
    """
    UrlValidator.validate_or_raise(url)


class FileValidator:
    """
    Validate local files created/used by downloaders.

    Checks:
    - path exists and is a file
    - size >= min_size_bytes
    - extension in allowed_extensions (if provided)
    """

    @staticmethod
    def _normalize_extensions(allowed_extensions: Optional[Iterable[str]]) -> Optional[Set[str]]:
        if allowed_extensions is None:
            return None
        normalized = {e.lower().lstrip(".") for e in allowed_extensions}
        _logger.debug("FileValidator normalized extensions: %s", normalized)
        return normalized

    @classmethod
    def is_valid(
        cls,
        path: Path,
        min_size_bytes: int = 1,
        allowed_extensions: Optional[Iterable[str]] = None,
    ) -> bool:
        """
        Return True if the file meets the validation criteria.

        :param path: Path of the file to validate.
        :param min_size_bytes: minimal acceptable file size in bytes (default 1).
        :param allowed_extensions: optional iterable of allowed file extensions (e.g. ["mp4", ".mkv"]).
        """
        try:
            path = path.resolve()
        except Exception:
            _logger.debug("FileValidator.is_valid: cannot resolve path=%r", path)
            return False

        if not path.exists() or not path.is_file():
            _logger.debug("FileValidator.is_valid: path missing or not a file: %s", path)
            return False

        try:
            size = path.stat().st_size
        except Exception:
            _logger.debug("FileValidator.is_valid: failed to stat file: %s", path)
            return False

        if size < max(0, int(min_size_bytes)):
            _logger.debug("FileValidator.is_valid: file too small (%d < %d): %s", size, min_size_bytes, path)
            return False

        normalized = cls._normalize_extensions(allowed_extensions)
        if normalized is not None:
            suffix = path.suffix.lower().lstrip(".")
            if suffix not in normalized:
                _logger.debug("FileValidator.is_valid: extension %r not in allowed %s for file %s", suffix, normalized, path)
                return False

        _logger.debug("FileValidator.is_valid: file %s passed validation", path)
        return True

    @classmethod
    def validate_or_raise(
        cls,
        path: Path,
        min_size_bytes: int = 1,
        allowed_extensions: Optional[Iterable[str]] = None,
    ) -> None:
        """
        Validate file or raise ValueError with descriptive message.

        :param path: Path object for the file to validate.
        :param min_size_bytes: minimal acceptable file size in bytes.
        :param allowed_extensions: optional iterable of allowed extensions.
        :raises ValueError: with explanation when validation fails.
        """
        try:
            path = path.resolve()
        except Exception:
            raise ValueError(f"Invalid path: {path!r}")

        if not path.exists():
            raise ValueError(f"File does not exist: {path}")

        if not path.is_file():
            raise ValueError(f"Path is not a file: {path}")

        try:
            size = path.stat().st_size
        except Exception as exc:
            raise ValueError(f"Unable to stat file {path}: {exc}")

        if size < max(0, int(min_size_bytes)):
            raise ValueError(f"File {path} too small: {size} bytes (minimum {min_size_bytes})")

        normalized = cls._normalize_extensions(allowed_extensions)
        if normalized is not None:
            suffix = path.suffix.lower().lstrip(".")
            if suffix not in normalized:
                raise ValueError(f"File extension .{suffix} not allowed (allowed: {sorted(normalized)})")