from __future__ import annotations

from pathlib import Path
import logging
from typing import List, Optional

from universal_video_ai.config import COOKIE_DIR

__all__ = ["CookieManager"]


class CookieManager:
    """
    Manage cookie files stored in a project cookie directory.

    Responsibilities:
    - Save cookie content for a given domain.
    - Find existing cookie files matching a domain.
    - Load cookie file content.
    - Provide downloader CLI args (e.g., ["--cookies", "<path>"]) for a given domain.
    - Clear cookie files by domain or clear all.
    """

    def __init__(self, cookie_dir: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize CookieManager.

        :param cookie_dir: Directory to store cookie files. If None uses `COOKIE_DIR` from config.
        :param logger: Optional logger to use. If None a module logger is used.
        """
        self.cookie_dir: Path = (cookie_dir or COOKIE_DIR).resolve()
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

        self.logger: logging.Logger = logger or logging.getLogger(__name__)
        self.logger.debug("CookieManager initialized with cookie_dir=%s", str(self.cookie_dir))

    def list_cookies(self) -> List[Path]:
        """
        List candidate cookie files in the cookie directory.

        Returns:
            List[Path]: list of file paths (not directories).
        """
        files = [p for p in self.cookie_dir.iterdir() if p.is_file()]
        self.logger.debug("Found %d candidate cookie files", len(files))
        return files

    def find_cookie_for_domain(self, domain: str) -> Optional[Path]:
        """
        Find a cookie file that best matches the given domain.

        Strategy:
        - Prefer files whose name contains the domain (case-insensitive).
        - Fallback to a generic "cookies.txt" file if present.
        - If multiple matches exist, returns the first found.

        :param domain: domain string, e.g. "youtube.com" or "youtu.be"
        :return: Path to cookie file or None if not found
        """
        if not domain:
            return None

        domain_norm = domain.lower().strip()
        self.logger.debug("Searching cookie for domain=%s", domain_norm)

        candidates = self.list_cookies()

        # First pass: filename contains domain
        for p in candidates:
            name = p.name.lower()
            if domain_norm in name:
                self.logger.debug("Matched cookie file by name: %s", str(p))
                return p

        # Second pass: exact domain-like basename without suffix
        for p in candidates:
            stem = p.stem.lower()
            if stem == domain_norm.replace(".", "_") or stem == domain_norm.replace(".", ""):
                self.logger.debug("Matched cookie file by stem: %s", str(p))
                return p

        # Third pass: generic cookies file e.g., cookies.txt
        for p in candidates:
            if p.name.lower() in ("cookies.txt", "cookie.txt", "cookies"):
                self.logger.debug("Using generic cookie file: %s", str(p))
                return p

        self.logger.debug("No cookie file found for domain=%s", domain_norm)
        return None

    def load_cookie_file(self, cookie_path: Path) -> str:
        """
        Read and return the content of a cookie file.

        :param cookie_path: Path to the cookie file
        :return: file content as text
        :raises FileNotFoundError: if cookie_path does not exist
        """
        cookie_path = cookie_path.resolve()
        self.logger.debug("Loading cookie file: %s", str(cookie_path))
        return cookie_path.read_text(encoding="utf-8")

    def save_cookie(self, domain: str, cookie_text: str) -> Path:
        """
        Save cookie content to a file named after the domain.

        :param domain: domain to associate the cookie with (e.g., "example.com")
        :param cookie_text: raw cookie content (as provided by user or export)
        :return: Path to saved cookie file
        """
        if not domain:
            raise ValueError("domain must be a non-empty string")

        safe_name = domain.lower().replace("/", "_").replace(":", "_").replace(" ", "_")
        filename = f"{safe_name}.cookies.txt"
        path = (self.cookie_dir / filename).resolve()

        self.logger.debug("Saving cookie for domain=%s to %s", domain, str(path))
        path.write_text(cookie_text, encoding="utf-8")
        return path

    def get_downloader_args(self, domain: str) -> List[str]:
        """
        Return command-line arguments for downloaders to use the cookie file for a domain.

        Example:
            ["--cookies", "/path/to/cookies.txt"]

        :param domain: domain to find cookie for
        :return: list of args (empty if no cookie found)
        """
        cookie_file = self.find_cookie_for_domain(domain)
        if cookie_file:
            args = ["--cookies", str(cookie_file)]
            self.logger.debug("Downloader args for domain=%s: %s", domain, args)
            return args

        self.logger.debug("No downloader args for domain=%s (no cookie)", domain)
        return []

    def clear_cookie(self, domain: Optional[str] = None) -> None:
        """
        Remove cookie files.

        :param domain: domain to remove cookies for. If None, remove all cookie files in the directory.
        """
        if domain:
            cookie_file = self.find_cookie_for_domain(domain)
            if cookie_file and cookie_file.exists():
                cookie_file.unlink()
                self.logger.debug("Removed cookie file: %s", str(cookie_file))
            else:
                self.logger.debug("No cookie file to remove for domain=%s", domain)
            return

        # Remove all files in cookie directory
        for p in self.list_cookies():
            try:
                p.unlink()
                self.logger.debug("Removed cookie file: %s", str(p))
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Failed to remove cookie file %s: %s", str(p), exc)