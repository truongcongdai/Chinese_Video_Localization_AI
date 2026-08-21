"""Provider contracts and the local-only Ollama implementation for CP4."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import socket
from typing import Any, Callable, Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)


class AIProvider(Protocol):
    """Structured-generation boundary used by the Content Brain service."""

    @property
    def name(self) -> str: ...

    def status(self) -> "ProviderStatus": ...

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        top_p: float,
        num_predict: int,
    ) -> str: ...


@dataclass(frozen=True)
class ProviderStatus:
    enabled: bool
    reachable: bool
    configured_model: Optional[str]
    model_available: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class OllamaProviderError(RuntimeError):
    """Sanitized, user-actionable local provider failure."""


class OllamaTimeoutError(OllamaProviderError):
    pass


class OllamaEmptyResponseError(OllamaProviderError):
    pass


class OllamaThinkUnsupportedError(OllamaProviderError):
    """The local Ollama API explicitly rejected the optional think field."""

    pass


Transport = Callable[[str, str, Optional[dict[str, Any]], float], dict[str, Any]]


class OllamaProvider:
    """Small stdlib HTTP adapter for Ollama's local API.

    Construction is side-effect free. Status and generation calls are the only
    operations that contact the configured local endpoint; models are never
    pulled or otherwise installed by this application.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        model: str,
        timeout_seconds: float = 120,
        transport: Optional[Transport] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.model = (model or "").strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._transport = transport or self._http_json

    @property
    def name(self) -> str:
        return "ollama"

    @staticmethod
    def _http_json(
        method: str,
        url: str,
        payload: Optional[dict[str, Any]],
        timeout: float,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicitly configured local service
                raw = response.read().decode("utf-8", errors="replace")
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaTimeoutError(
                f"Content Brain timed out after {int(timeout)} seconds."
            ) from exc
        except HTTPError as exc:
            if exc.code == 404:
                raise OllamaProviderError(
                    "Configured Ollama model is not available locally."
                ) from exc
            try:
                error_text = exc.read(2048).decode("utf-8", errors="replace").casefold()
            except Exception:
                error_text = ""
            if exc.code in {400, 422} and "think" in error_text and any(
                marker in error_text for marker in ("unknown", "unsupported", "unrecognized")
            ):
                raise OllamaThinkUnsupportedError(
                    "This Ollama version does not support the think field."
                ) from exc
            raise OllamaProviderError("Ollama returned an HTTP error.") from exc
        except URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise OllamaTimeoutError(
                    f"Content Brain timed out after {int(timeout)} seconds."
                ) from exc
            raise OllamaProviderError(
                "Ollama is not running. Start Ollama and try again."
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise OllamaProviderError(
                "Ollama is not running. Start Ollama and try again."
            ) from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaProviderError("Ollama returned a malformed response.") from exc
        if not isinstance(parsed, dict):
            raise OllamaProviderError("Ollama returned a malformed response.")
        return parsed

    def _models(self) -> list[str]:
        payload = self._transport("GET", f"{self.base_url}/api/tags", None, min(10, self.timeout_seconds))
        rows = payload.get("models")
        if not isinstance(rows, list):
            raise OllamaProviderError("Ollama returned a malformed model list.")
        return [str(row.get("name") or row.get("model") or "") for row in rows if isinstance(row, dict)]

    def status(self) -> ProviderStatus:
        if not self.enabled:
            return ProviderStatus(False, False, self.model or None, False, "Local Content Brain is disabled.")
        try:
            models = self._models()
        except OllamaProviderError as exc:
            return ProviderStatus(True, False, self.model or None, False, str(exc))
        if not self.model:
            return ProviderStatus(
                True, True, None, False, "Ollama is installed but no model is selected."
            )
        available = self.model in models
        return ProviderStatus(
            True,
            True,
            self.model,
            available,
            "Ollama and the configured model are ready."
            if available
            else "Configured Ollama model is not available locally.",
        )

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        top_p: float,
        num_predict: int,
    ) -> str:
        if not self.enabled:
            raise OllamaProviderError("Local Content Brain is disabled.")
        if not self.model:
            raise OllamaProviderError("No local model is configured for Content Brain.")
        request_payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "temperature": float(temperature),
                "top_p": float(top_p),
                "num_predict": int(num_predict),
            },
        }
        try:
            payload = self._transport(
                "POST", f"{self.base_url}/api/chat", request_payload, self.timeout_seconds
            )
        except OllamaThinkUnsupportedError:
            # Older Ollama releases may reject a field they do not recognize.
            # Retry exactly once without it; structured JSON and all bounds stay intact.
            compatible_payload = dict(request_payload)
            compatible_payload.pop("think", None)
            logger.info(
                "Content Brain Ollama compatibility retry model=%s think_field=unsupported",
                self.model,
            )
            payload = self._transport(
                "POST", f"{self.base_url}/api/chat", compatible_payload, self.timeout_seconds
            )
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else payload.get("response")
        if not isinstance(content, str) or not content.strip():
            error = str(payload.get("error") or "").casefold()
            if "model" in error and ("not found" in error or "not available" in error):
                raise OllamaProviderError(
                    "Configured Ollama model is not available locally."
                )
            raise OllamaEmptyResponseError("Ollama returned an empty result.")
        return content.strip()
