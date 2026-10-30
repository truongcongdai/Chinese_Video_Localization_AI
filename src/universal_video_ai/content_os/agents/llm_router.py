"""
LLM router for Content OS.

Routes LLM requests to the appropriate provider (OpenAI, Ollama, etc.)
and handles structured output requests.
"""
import logging
import json
import os
import re
from typing import Optional, Dict, Any, Type
from pydantic import BaseModel

from ..config import CONTENT_OS_LLM_PROVIDER, CONTENT_OS_LLM_MODEL, CONTENT_OS_LLM_BASE_URL, CONTENT_OS_LLM_API_KEY
from ..exceptions import LLMOutputError, ProviderUnavailableError
from ..config import CONTENT_OS_PROVIDER_TIMEOUT_SECONDS
from universal_video_ai.network import NetworkRetryPolicy, post_with_retry
from universal_video_ai.provider_runtime import execute_provider_call

logger = logging.getLogger(__name__)


def _parse_json_content(content: str) -> Dict[str, Any]:
    """Parse JSON from strict JSON, fenced JSON, or prose-wrapped model output."""
    if not content:
        raise json.JSONDecodeError("empty response", "", 0)

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise json.JSONDecodeError("no JSON object found", content, 0)


class LLMRouter:
    """
    Routes LLM requests to configured providers.
    
    Supports:
    - OpenAI API (including compatible endpoints like Ollama's OpenAI-compatible mode)
    - Direct Ollama API
    - Future: Anthropic, Google, etc.
    """
    
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider or CONTENT_OS_LLM_PROVIDER
        self.model = model or CONTENT_OS_LLM_MODEL
        self.base_url = base_url or CONTENT_OS_LLM_BASE_URL
        self.api_key = api_key or CONTENT_OS_LLM_API_KEY
        self.logger = logging.getLogger(__name__)

    def _effective_provider(self) -> str:
        provider = (self.provider or "auto").lower()
        if provider != "auto":
            return provider
        if self.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY"):
            return "gemini"
        return "ollama"
    
    def invoke(
        self,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """
        Invoke the LLM with the given prompt.
        
        Args:
            prompt: The prompt to send
            output_schema: Optional Pydantic schema for structured output
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            The LLM output as a dictionary
            
        Raises:
            ProviderUnavailableError: If the provider is not available
            LLMOutputError: If the LLM output cannot be parsed
        """
        provider = self._effective_provider()
        self.logger.debug(f"Invoking LLM: provider={provider}, model={self.model}")
        
        handlers = {
            "openai": self._invoke_openai,
            "ollama": self._invoke_ollama,
            "gemini": self._invoke_gemini,
        }
        if provider not in handlers:
            raise ProviderUnavailableError(f"Unsupported LLM provider: {self.provider}")
        return execute_provider_call(
            provider, "content_os_generation",
            {"prompt": prompt, "schema": getattr(output_schema, "__name__", None)},
            live=lambda: handlers[provider](prompt, output_schema, temperature, max_tokens),
            mock=self._mock_output,
            category="llm",
            settings={"model": self.model, "temperature": temperature, "max_tokens": max_tokens},
        )
    
    def _invoke_openai(
        self,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """
        Invoke OpenAI-compatible API.
        
        This works with:
        - OpenAI API
        - Ollama in OpenAI-compatible mode
        - Other OpenAI-compatible endpoints
        """
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailableError("openai package is not installed") from exc
        client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "dummy-key",
            max_retries=2,
            timeout=CONTENT_OS_PROVIDER_TIMEOUT_SECONDS,
        )
        messages = [{"role": "user", "content": prompt}]
        response = client.chat.completions.create(
            model=self.model or "gpt-3.5-turbo",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=CONTENT_OS_PROVIDER_TIMEOUT_SECONDS,
        )
        content = response.choices[0].message.content
        if output_schema:
            try:
                return _parse_json_content(content)
            except json.JSONDecodeError as exc:
                raise LLMOutputError("OpenAI returned invalid structured output") from exc
        return {"raw_content": content}
    
    def _invoke_ollama(
        self,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """
        Invoke Ollama API directly.
        """
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        url = f"{base_url}/api/generate"
        effective_prompt = prompt
        if output_schema:
            effective_prompt = (
                f"{prompt}\n\n"
                "Return only one valid JSON object. Do not wrap it in Markdown. "
                "Do not include commentary before or after JSON."
            )
        payload = {
            "model": self.model or "llama2",
            "prompt": effective_prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if output_schema:
            payload["format"] = "json"
        response = post_with_retry(
            url, json=payload,
            policy=NetworkRetryPolicy(read_timeout=CONTENT_OS_PROVIDER_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        content = response.json().get("response", "")
        if output_schema:
            try:
                return _parse_json_content(content)
            except json.JSONDecodeError as exc:
                raise LLMOutputError("Ollama returned invalid structured output") from exc
        return {"raw_content": content}

    def _invoke_gemini(
        self,
        prompt: str,
        output_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """Invoke Gemini generateContent API."""
        api_key = self.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
        if not api_key:
            raise ProviderUnavailableError("Gemini API key is not configured")

        model = self.model or os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite"
        effective_prompt = prompt
        generation_config: Dict[str, Any] = {
            "temperature": temperature, "maxOutputTokens": max_tokens,
        }
        if output_schema:
            effective_prompt = (
                f"{prompt}\n\n"
                "Return only one valid JSON object. Do not wrap it in Markdown. "
                "Do not include commentary before or after JSON."
            )
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": effective_prompt}]}],
            "generationConfig": generation_config,
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        response = post_with_retry(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            policy=NetworkRetryPolicy(read_timeout=CONTENT_OS_PROVIDER_TIMEOUT_SECONDS),
        )
        if response.status_code == 400 and "responseMimeType" in response.text:
            payload["generationConfig"].pop("responseMimeType", None)
            response.close()
            response = post_with_retry(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                policy=NetworkRetryPolicy(read_timeout=CONTENT_OS_PROVIDER_TIMEOUT_SECONDS),
            )
        response.raise_for_status()
        data = response.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        content = "".join(str(part.get("text") or "") for part in parts).strip()
        if output_schema:
            try:
                return _parse_json_content(content)
            except json.JSONDecodeError as exc:
                raise LLMOutputError("Gemini returned invalid structured output") from exc
        return {"raw_content": content}
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return {"raw_content": "Mock LLM output - LLM not configured"}
