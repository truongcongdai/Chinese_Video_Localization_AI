from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import logging
import re

import requests

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishingLLMConfig:
    provider: str = "none"
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: int = 180


class PublishingLLMClient:
    def __init__(self, config: PublishingLLMConfig, logger=None):
        self.config = config
        self.logger = logger or _logger

    @property
    def available(self) -> bool:
        provider = (self.config.provider or "none").lower()
        if provider in {"gemini", "openai"}:
            return bool(self.config.api_key)
        if provider == "ollama":
            return True
        return False

    def generate_json(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        provider = (self.config.provider or "none").lower()
        try:
            if provider == "gemini":
                content = self._gemini(prompt)
            elif provider == "openai":
                content = self._openai(prompt)
            elif provider == "ollama":
                content = self._ollama(prompt)
            else:
                return None
            return _parse_json_object(content)
        except Exception as exc:
            self.logger.warning("Publishing LLM failed; using deterministic fallback: %s", exc)
            return None

    def _gemini(self, prompt: str) -> str:
        model = self.config.model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        response = requests.post(
            url,
            params={"key": self.config.api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.35,
                    "responseMimeType": "application/json",
                },
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"])

    def _openai(self, prompt: str) -> str:
        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={
                "model": self.config.model or "gpt-4.1-mini",
                "messages": [
                    {"role": "system", "content": "Return one valid JSON object only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.35,
                "response_format": {"type": "json_object"},
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])

    def _ollama(self, prompt: str) -> str:
        base = (self.config.base_url or "http://127.0.0.1:11434").rstrip("/")
        response = requests.post(
            f"{base}/api/chat",
            json={
                "model": self.config.model or "qwen2.5:7b",
                "messages": [
                    {"role": "system", "content": "Return one valid JSON object only."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.35},
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        return str(response.json().get("message", {}).get("content") or "")


def _parse_json_object(content: str) -> Dict[str, Any]:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM did not return a JSON object")
    candidate = text[start:end + 1]
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("LLM output is not a JSON object")
    return parsed
