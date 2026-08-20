"""Provider contracts for future local or optional AI implementations."""

from __future__ import annotations

from typing import Protocol


class AIProvider(Protocol):
    """Text-generation boundary; business services must depend on this contract."""

    @property
    def name(self) -> str:
        """Stable provider identifier, for example ``ollama``."""

        ...

    def is_available(self) -> bool:
        """Return a real availability check result without exposing secrets."""

        ...

    def generate_text(self, prompt: str, *, system_prompt: str = "") -> str:
        """Generate text for a caller-supplied prompt."""

        ...
