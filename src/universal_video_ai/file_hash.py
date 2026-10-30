"""Content fingerprints with memory usage independent of media size."""
from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["sha256_file"]


def sha256_file(path: Path) -> str:
    """Hash the complete file using a reusable buffer managed by hashlib."""
    with path.open("rb") as stream:
        if hasattr(hashlib, "file_digest"):
            return hashlib.file_digest(stream, "sha256").hexdigest()
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()
