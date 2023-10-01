"""Registry and chain for regulation sources."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Iterable, Optional, Sequence

from django.conf import settings

from .base import RegulationFinding, RegulationSource, RegulationSourceError

logger = logging.getLogger(__name__)

SourceFactory = Callable[[], RegulationSource]

_REGISTRY: dict[str, SourceFactory] = {}
_CACHE: dict[str, RegulationSource] = {}
_LOCK = threading.Lock()

DEFAULT_CHAIN = ("curated", "llm")


def register_source(name: str, factory: SourceFactory, *, replace: bool = False) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("A regulation source needs a name.")
    if key in _REGISTRY and not replace:
        raise ValueError(f"Regulation source {key!r} is already registered.")
    with _LOCK:
        _REGISTRY[key] = factory
        _CACHE.pop(key, None)


def available_sources() -> list[str]:
    return sorted(_REGISTRY)


def reset_source_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def get_source(name: str) -> RegulationSource:
    key = name.strip().lower()
    if key not in _REGISTRY:
        raise KeyError(f"Unknown regulation source {name!r}. Known: {available_sources()}")
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with _LOCK:
        source = _REGISTRY[key]()
        _CACHE[key] = source
    return source


def configured_chain() -> tuple[str, ...]:
    names = getattr(settings, "REGULATION_SOURCES", None) or DEFAULT_CHAIN
    return tuple(str(name).strip().lower() for name in names if str(name).strip())


class RegulationSourceChain:
    """
    Consult sources in order and return the first real answer.

    A source that does not know returns ``None`` and the chain moves on; a
    source that is configured but broken is logged and skipped, so one dead
    upstream cannot take the feature down.
    """

    name = "chain"

    def __init__(self, sources: Optional[Sequence[RegulationSource]] = None) -> None:
        self._sources = list(sources) if sources is not None else None

    @property
    def sources(self) -> list[RegulationSource]:
        if self._sources is not None:
            return self._sources
        resolved: list[RegulationSource] = []
        for name in configured_chain():
            try:
                resolved.append(get_source(name))
            except KeyError:
                logger.warning("REGULATION_SOURCES names an unknown source: %r", name)
        return resolved

    def is_available(self) -> bool:
        return any(source.is_available() for source in self.sources)

    def lookup(self, query: str) -> Optional[RegulationFinding]:
        errors: list[str] = []
        for source in self.sources:
            if not source.is_available():
                logger.debug("Regulation source %s is unavailable; skipping.", source.name)
                continue
            try:
                finding = source.lookup(query)
            except RegulationSourceError as exc:
                logger.warning("Regulation source %s failed: %s", source.name, exc)
                errors.append(f"{source.name}: {exc}")
                continue
            if finding is not None:
                return finding

        if errors:
            raise RegulationSourceError("; ".join(errors))
        return None


def _register_builtins() -> None:
    from .llm_source import LLMRegulationSource
    from .static_source import StaticRegulationSource

    register_source("curated", StaticRegulationSource, replace=True)
    register_source("llm", LLMRegulationSource, replace=True)


_register_builtins()


def iter_sources(names: Iterable[str]) -> list[RegulationSource]:
    """Resolve a list of names, skipping any that are not registered."""
    resolved = []
    for name in names:
        try:
            resolved.append(get_source(name))
        except KeyError:
            logger.warning("Ignoring unknown regulation source %r", name)
    return resolved
