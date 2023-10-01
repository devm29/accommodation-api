"""Provider registry.

``get_llm_provider()`` is the only thing application code calls. Which
implementation it returns is a setting, so adding Anthropic or a local model
means registering a factory - not touching a view, a task or a service.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from django.conf import settings

from .base import LLMProvider

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[], LLMProvider]

_REGISTRY: dict[str, ProviderFactory] = {}
_CACHE: dict[str, LLMProvider] = {}
_LOCK = threading.Lock()


def register_provider(name: str, factory: ProviderFactory, *, replace: bool = False) -> None:
    """Make ``name`` selectable through ``LLM_PROVIDER`` and ``get_llm_provider``."""
    key = name.strip().lower()
    if not key:
        raise ValueError("A provider needs a name.")
    if key in _REGISTRY and not replace:
        raise ValueError(f"Provider {key!r} is already registered.")
    with _LOCK:
        _REGISTRY[key] = factory
        _CACHE.pop(key, None)


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def reset_provider_cache() -> None:
    """Drop memoised instances. Tests use this; so does a settings change."""
    with _LOCK:
        _CACHE.clear()


def get_llm_provider(name: Optional[str] = None) -> LLMProvider:
    """
    Return the configured provider.

    Resolution order: the explicit argument, then ``settings.LLM_PROVIDER``,
    then ``openai`` when a key is present, then the null provider - which
    reports itself unavailable rather than failing at import.
    """
    key = (name or getattr(settings, "LLM_PROVIDER", "") or "").strip().lower()
    if not key:
        key = "openai" if getattr(settings, "OPENAI_API_KEY", None) else "null"

    if key not in _REGISTRY:
        logger.warning("Unknown LLM_PROVIDER %r; falling back to the null provider.", key)
        key = "null"

    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    with _LOCK:
        provider = _REGISTRY[key]()
        _CACHE[key] = provider
    return provider


def _register_builtins() -> None:
    from .demo_provider import DemoProvider
    from .null_provider import NullProvider
    from .openai_provider import OpenAIProvider

    def _openai() -> LLMProvider:
        return OpenAIProvider(
            api_key=getattr(settings, "OPENAI_API_KEY", None),
            model=getattr(settings, "LLM_MODEL", None) or "gpt-4",
            model_overrides=getattr(settings, "LLM_MODEL_OVERRIDES", {}) or {},
        )

    register_provider("openai", _openai, replace=True)
    register_provider("demo", DemoProvider, replace=True)
    register_provider("null", NullProvider, replace=True)


_register_builtins()
