# src/universal_video_ai/models/manager.py
from __future__ import annotations

import importlib.util
import logging
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["ModelManager", "ModelCheckResult"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCheckResult:
    """
    Result for a single model/tool check.

    Attributes:
        name: logical name (e.g., "ffmpeg", "whisper")
        available: whether the tool is available
        method: 'executable' or 'python' (how availability is detected)
        details: optional textual details (path, module name, etc.)
    """

    name: str
    available: bool
    method: str
    details: Optional[str] = None


class ModelManager:
    """
    Helper to detect availability of tools required by audio pipeline components
    (ffmpeg, ffprobe, demucs, whisper, torch, edge-tts, ...).

    Responsibilities:
    - Check for executables on PATH (using shutil.which).
    - Check for Python packages (using importlib.util.find_spec).
    - Provide human-readable install suggestions per platform.
    - Produce a report dict and an actionable list of missing items.

    This class is read-only (no side effects) and safe to call in tests or at
    application startup to give helpful guidance to developers / operators.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        self.platform = platform.system().lower()
        self.logger.debug("ModelManager initialized on platform=%s", self.platform)

    # --- low-level checks -------------------------------------------------
    def check_executable(self, name: str) -> bool:
        """
        Return True if an executable named `name` is discoverable on PATH.
        """
        path = shutil.which(name)
        available = path is not None
        self.logger.debug("check_executable %s -> %s (path=%s)", name, available, path)
        return available

    def check_python_package(self, module_name: str) -> bool:
        """
        Return True if a Python package `module_name` is importable.
        """
        spec = importlib.util.find_spec(module_name)
        available = spec is not None
        self.logger.debug("check_python_package %s -> %s (spec=%s)", module_name, available, spec)
        return available

    # --- component-specific checks ---------------------------------------
    def check_ffmpeg_available(self) -> bool:
        return self.check_executable("ffmpeg")

    def check_ffprobe_available(self) -> bool:
        return self.check_executable("ffprobe")

    def check_demucs_available(self) -> bool:
        # demucs can be a CLI or a python package; check both
        return self.check_executable("demucs") or self.check_python_package("demucs")

    def check_whisper_available(self) -> bool:
        # openai-whisper installs as module 'whisper'
        return self.check_python_package("whisper")

    def check_torch_available(self) -> bool:
        return self.check_python_package("torch")

    def check_edge_tts_available(self) -> bool:
        # edge-tts CLI commonly named 'edge-tts' and/or Python package 'edge_tts'
        return self.check_executable("edge-tts") or self.check_python_package("edge_tts")

    # --- reporting / suggestions -----------------------------------------
    def suggest_install_for(self, name: str) -> str:
        """
        Return a short, actionable install suggestion for `name`, adapted to OS.
        This is only a hint — the user may prefer other installation methods.
        """
        os_name = self.platform
        if name in ("ffmpeg", "ffprobe"):
            if os_name == "linux":
                return "Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg"
            if os_name == "windows":
                return "Windows: Install ffmpeg from https://ffmpeg.org/download.html or use choco/winget: choco install ffmpeg"
            if os_name == "darwin":
                return "macOS: brew install ffmpeg"
            return "Install ffmpeg for your OS (see https://ffmpeg.org/download.html)"

        if name == "demucs":
            return "pip install demucs  # or see https://github.com/facebookresearch/demucs for GPU setup"

        if name == "whisper":
            return "pip install -U openai-whisper  # may require torch; see model docs"

        if name == "torch":
            # Torch install depends on CUDA / CPU — provide link
            return "Install PyTorch appropriate for your system: https://pytorch.org/get-started/locally/"

        if name == "edge-tts":
            return "pip install edge-tts  # or use pipx/pip"

        # fallback
        return f"Please install {name} (use your platform package manager or pip)."

    def explain_missing(self) -> List[str]:
        """
        Return a list of human-readable messages describing missing components
        and how to install them.
        """
        checks = {
            "ffmpeg": self.check_ffmpeg_available(),
            "ffprobe": self.check_ffprobe_available(),
            "demucs": self.check_demucs_available(),
            "whisper": self.check_whisper_available(),
            "torch": self.check_torch_available(),
            "edge-tts": self.check_edge_tts_available(),
        }

        messages: List[str] = []
        for name, available in checks.items():
            if not available:
                suggestion = self.suggest_install_for(name)
                messages.append(f"{name} not found. Suggestion: {suggestion}")
                self.logger.debug("explain_missing: %s missing", name)
            else:
                self.logger.debug("explain_missing: %s available", name)

        if not messages:
            messages.append("All required tools appear to be available.")
        return messages

    def report(self) -> Dict[str, ModelCheckResult]:
        """
        Return a dict of ModelCheckResult objects keyed by component name.
        """
        results: Dict[str, ModelCheckResult] = {}

        # executables
        for exe in ("ffmpeg", "ffprobe", "demucs", "edge-tts"):
            available = self.check_executable(exe) if exe in ("ffmpeg", "ffprobe") else (
                self.check_executable(exe) or self.check_python_package(exe.replace("-", "_"))
            )
            method = "executable" if shutil.which(exe) else "python"
            details = shutil.which(exe) or None
            results[exe] = ModelCheckResult(name=exe, available=available, method=method, details=details)

        # python packages
        for pkg in ("whisper", "torch", "demucs", "edge_tts"):
            available = self.check_python_package(pkg)
            method = "python"
            details = pkg if available else None
            results[pkg] = ModelCheckResult(name=pkg, available=available, method=method, details=details)

        return results

    # -- convenience summary ----------------------------------------------
    def summary(self) -> str:
        """
        Return a short multi-line summary suitable for logging or display.
        """
        report = self.report()
        lines: List[str] = []
        for key in sorted(report.keys()):
            r = report[key]
            status = "OK" if r.available else "MISSING"
            details = f" ({r.details})" if r.details else ""
            lines.append(f"{key}: {status}{details}")
        return "\n".join(lines)