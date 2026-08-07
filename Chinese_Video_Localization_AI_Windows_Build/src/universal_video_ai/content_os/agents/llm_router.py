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
        
        if provider == "openai":
            return self._invoke_openai(prompt, output_schema, temperature, max_tokens)
        elif provider == "ollama":
            return self._invoke_ollama(prompt, output_schema, temperature, max_tokens)
        elif provider == "gemini":
            return self._invoke_gemini(prompt, output_schema, temperature, max_tokens)
        else:
            raise ProviderUnavailableError(f"Unsupported LLM provider: {self.provider}")
    
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
            # Try to import openai
            try:
                from openai import OpenAI
            except ImportError:
                self.logger.warning("openai package not installed, using mock output")
                return self._mock_output()
            
            client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key or "dummy-key",  # Ollama doesn't require API key
            )
            
            # Build messages
            messages = [{"role": "user", "content": prompt}]
            
            # Call the API
            response = client.chat.completions.create(
                model=self.model or "gpt-3.5-turbo",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=300,  # Increased timeout
            )
            
            # Extract content
            content = response.choices[0].message.content
            
            if output_schema:
                try:
                    return _parse_json_content(content)
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Failed to parse LLM output as JSON: {e}")
                    return {"raw_content": content}
            
            return {"raw_content": content}
            
        except Exception as e:
            self.logger.warning(f"OpenAI API call failed: {e}, using mock output")
            return self._mock_output()
    
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
        try:
            import requests
            
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
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }
            if output_schema:
                payload["format"] = "json"
            
            response = requests.post(url, json=payload, timeout=300)
            response.raise_for_status()
            
            data = response.json()
            content = data.get("response", "")
            
            if output_schema:
                try:
                    return _parse_json_content(content)
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Failed to parse Ollama output as JSON: {e}")
                    return {"raw_content": content}
            
            return {"raw_content": content}
            
        except ImportError:
            self.logger.warning("requests package not installed, using mock output")
            return self._mock_output()
        except Exception as e:
            self.logger.warning(f"Ollama API call failed: {e}, using mock output")
            return self._mock_output()

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
            self.logger.warning("GEMINI_API_KEY/GOOGLE_AI_API_KEY not configured, using mock output")
            return self._mock_output()

        try:
            import requests

            model = self.model or os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite"
            effective_prompt = prompt
            generation_config: Dict[str, Any] = {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
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
            response = requests.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=300,
            )
            if response.status_code == 400 and "responseMimeType" in response.text:
                payload["generationConfig"].pop("responseMimeType", None)
                response = requests.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=300,
                )
            response.raise_for_status()
            data = response.json()
            parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            content = "".join(str(part.get("text") or "") for part in parts).strip()

            if output_schema:
                try:
                    return _parse_json_content(content)
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Failed to parse Gemini output as JSON: {e}")
                    return {"raw_content": content}
            return {"raw_content": content}
        except ImportError:
            self.logger.warning("requests package not installed, using mock output")
            return self._mock_output()
        except Exception as e:
            self.logger.warning(f"Gemini API call failed: {e}, using mock output")
            return self._mock_output()
    
    def _mock_output(self) -> Dict[str, Any]:
        """Return mock output for testing."""
        return {"raw_content": "Mock LLM output - LLM not configured"}
