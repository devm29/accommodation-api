"""
Test helpers for the two seams.

Tests install a scripted provider or source through the same registry the
application uses, so they exercise the real prompt building, parsing and
persistence rather than asserting on a mock's return value. Nothing in the
suite may reach a paid endpoint.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional, Sequence

from django.test import override_settings

from .llm import Completion, LLMError, Message, register_provider, reset_provider_cache
from .llm.base import to_messages
from .regulation_sources import register_source, reset_source_cache

TEST_PROVIDER_NAME = "scripted"
TEST_SOURCE_NAME = "scripted"


class ScriptedProvider:
    """
    Returns whatever it was told to return, and records what it was asked.

    ``responses`` may be a single string, a list consumed in order, or a dict
    keyed by purpose. ``error`` makes every call raise.
    """

    name = TEST_PROVIDER_NAME

    def __init__(
        self,
        responses: Any = "",
        *,
        error: Optional[Exception] = None,
        available: bool = True,
    ) -> None:
        self.responses = responses
        self.error = error
        self.available = available
        self.calls: list[tuple[str, list[Message]]] = []

    def is_available(self) -> bool:
        return self.available

    def complete(
        self,
        messages: Sequence[Any],
        *,
        purpose: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Completion:
        self.calls.append((purpose, to_messages(messages)))
        if self.error is not None:
            raise self.error
        return Completion(text=self._text(purpose), model=model or "scripted-1", provider=self.name)

    def _text(self, purpose: str) -> str:
        if isinstance(self.responses, dict):
            return str(self.responses.get(purpose, ""))
        if isinstance(self.responses, (list, tuple)):
            if not self.responses:
                raise LLMError("ScriptedProvider ran out of scripted responses.")
            return str(self.responses.pop(0))
        return str(self.responses)


class ScriptedSource:
    """A regulation source that answers with whatever it was handed."""

    name = TEST_SOURCE_NAME
    is_offline = True

    def __init__(self, finding=None, *, error: Optional[Exception] = None, available=True):
        self.finding = finding
        self.error = error
        self.available = available
        self.queries: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def lookup(self, query: str):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.finding


@contextmanager
def use_provider(provider):
    """Install ``provider`` as the configured LLM for the duration of the block."""
    register_provider(TEST_PROVIDER_NAME, lambda: provider, replace=True)
    reset_provider_cache()
    with override_settings(LLM_PROVIDER=TEST_PROVIDER_NAME):
        try:
            yield provider
        finally:
            reset_provider_cache()


@contextmanager
def use_sources(*names: str, **sources):
    """
    Make ``names`` the configured chain, registering any keyword-supplied
    source instances first: ``use_sources("curated", scripted=ScriptedSource(...))``.
    """
    for name, instance in sources.items():
        register_source(name, (lambda bound=instance: bound), replace=True)
    reset_source_cache()
    chain = list(names) + list(sources)
    with override_settings(REGULATION_SOURCES=chain):
        try:
            yield
        finally:
            reset_source_cache()
