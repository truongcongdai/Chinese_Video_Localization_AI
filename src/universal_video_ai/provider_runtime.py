"""Explicit execution controls for external and potentially costly providers.

Normal application code may use LIVE, but test processes default to MOCK and
LIVE always requires RUN_LIVE_TESTS=1.  OAuth/token exchanges are deliberately
not cacheable; callers must opt in to caching each safe operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional


class ProviderMode(str, Enum):
    MOCK = "MOCK"
    CACHE = "CACHE"
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class ProviderBudgetExceeded(RuntimeError):
    pass


class LiveProviderDisabled(RuntimeError):
    pass


@dataclass
class ProviderBudget:
    max_llm_calls: Optional[int] = None
    max_external_api_calls: Optional[int] = None
    max_tts_requests: Optional[int] = None
    max_upload_attempts: Optional[int] = None
    max_live_acceptance_calls: Optional[int] = None


@dataclass
class ProviderCostReport:
    provider_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    mock_calls: int = 0
    dry_run_calls: int = 0
    live_calls: int = 0
    llm_calls: int = 0
    tts_requests: int = 0
    upload_attempts: int = 0
    by_provider: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_calls": self.provider_calls,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "mock_calls": self.mock_calls,
            "dry_run_calls": self.dry_run_calls,
            "live_calls": self.live_calls,
            "llm_calls": self.llm_calls,
            "tts_requests": self.tts_requests,
            "upload_attempts": self.upload_attempts,
            "by_provider": dict(self.by_provider),
        }


_LOCK = threading.Lock()
_REPORT = ProviderCostReport()
_MEMORY_CACHE: Dict[str, Any] = {}
_SECRET_KEYS = {"access_token", "refresh_token", "authorization", "password", "secret", "api_key"}


def default_provider_mode() -> ProviderMode:
    raw = os.getenv("PROVIDER_EXECUTION_MODE", "").strip().upper()
    if raw:
        try:
            return ProviderMode(raw)
        except ValueError as exc:
            raise ValueError(f"Unsupported provider mode: {raw}") from exc
    return ProviderMode.MOCK if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in os.sys.modules else ProviderMode.DRY_RUN


def reset_cost_report(*, clear_cache: bool = False) -> None:
    global _REPORT
    with _LOCK:
        _REPORT = ProviderCostReport()
        if clear_cache:
            _MEMORY_CACHE.clear()


def get_cost_report() -> ProviderCostReport:
    with _LOCK:
        return ProviderCostReport(**_REPORT.to_dict())


def _safe_normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        output = {}
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            normalized_key = str(key).strip().lower()
            if (
                normalized_key in _SECRET_KEYS
                or normalized_key.endswith(("_token", "_secret", "_password", "_api_key"))
            ):
                continue
            output[str(key)] = _safe_normalize(child)
        return output
    if isinstance(value, (list, tuple)):
        return [_safe_normalize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def cache_key(provider: str, operation: str, payload: Any, settings: Any = None) -> str:
    normalized = json.dumps(
        {"provider": provider, "operation": operation, "payload": _safe_normalize(payload), "settings": _safe_normalize(settings)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _budget_value(report: ProviderCostReport, category: str) -> int:
    return {
        "llm": report.llm_calls,
        "tts": report.tts_requests,
        "upload": report.upload_attempts,
        "external": report.provider_calls,
        "live_acceptance": report.live_calls,
    }.get(category, report.provider_calls)


def _budget_limit(budget: ProviderBudget, category: str) -> Optional[int]:
    return {
        "llm": budget.max_llm_calls,
        "tts": budget.max_tts_requests,
        "upload": budget.max_upload_attempts,
        "external": budget.max_external_api_calls,
        "live_acceptance": budget.max_live_acceptance_calls,
    }.get(category, budget.max_external_api_calls)


def execute_provider_call(
    provider: str,
    operation: str,
    payload: Any,
    *,
    live: Callable[[], Any],
    mock: Optional[Callable[[], Any]] = None,
    mode: Optional[ProviderMode | str] = None,
    budget: Optional[ProviderBudget] = None,
    category: str = "external",
    cacheable: bool = False,
    settings: Any = None,
) -> Any:
    selected = ProviderMode(str(mode or default_provider_mode()).upper().split(".")[-1])
    sensitive_operation = any(
        marker in str(operation).lower() for marker in ("oauth", "token_exchange", "authorization")
    )
    if cacheable and sensitive_operation:
        raise ValueError("OAuth and authorization responses cannot be provider-cached.")
    live_opt_in = os.getenv("RUN_LIVE_TESTS") == "1"
    if selected is ProviderMode.LIVE and not live_opt_in:
        raise LiveProviderDisabled("LIVE provider calls require RUN_LIVE_TESTS=1")
    key = cache_key(provider, operation, payload, settings)
    with _LOCK:
        if selected is ProviderMode.CACHE and cacheable and key in _MEMORY_CACHE:
            _REPORT.cache_hits += 1
            return _MEMORY_CACHE[key]
        if selected is ProviderMode.CACHE:
            _REPORT.cache_misses += 1
        if budget is not None:
            if (
                budget.max_external_api_calls is not None
                and _REPORT.provider_calls >= int(budget.max_external_api_calls)
            ):
                raise ProviderBudgetExceeded(
                    f"external provider budget exceeded ({budget.max_external_api_calls})"
                )
            limit = _budget_limit(budget, category)
            if limit is not None and _budget_value(_REPORT, category) >= int(limit):
                raise ProviderBudgetExceeded(f"{category} provider budget exceeded ({limit})")
        _REPORT.provider_calls += 1
        _REPORT.by_provider[provider] = _REPORT.by_provider.get(provider, 0) + 1
        if category == "llm":
            _REPORT.llm_calls += 1
        elif category == "tts":
            _REPORT.tts_requests += 1
        elif category == "upload":
            _REPORT.upload_attempts += 1
    if selected is ProviderMode.MOCK:
        if mock is None:
            raise RuntimeError(f"No deterministic mock supplied for {provider}.{operation}")
        with _LOCK:
            _REPORT.mock_calls += 1
        return mock()
    if selected is ProviderMode.CACHE and not live_opt_in:
        if mock is None:
            raise LiveProviderDisabled(
                f"Cache miss for {provider}.{operation}; LIVE fallback requires RUN_LIVE_TESTS=1"
            )
        with _LOCK:
            _REPORT.mock_calls += 1
        result = mock()
        if cacheable:
            with _LOCK:
                _MEMORY_CACHE[key] = result
        return result
    if selected is ProviderMode.DRY_RUN:
        with _LOCK:
            _REPORT.dry_run_calls += 1
        return {"dry_run": True, "provider": provider, "operation": operation}
    result = live()
    with _LOCK:
        _REPORT.live_calls += 1
        if selected is ProviderMode.CACHE and cacheable:
            _MEMORY_CACHE[key] = result
    return result
