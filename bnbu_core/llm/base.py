"""Vendor-neutral types for chat completions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence, runtime_checkable


class Purpose:
    """
    What a completion is *for*.

    It travels with every call so providers can route (a cheap model for chat,
    a stronger one for extraction) and so the demo provider knows which canned
    answer to hand back. Free-form strings are allowed; these are the ones the
    product uses.
    """

    LEASE_ANALYSIS = "lease_analysis"
    LEASE_CHAT = "lease_chat"
    REGULATION_LOOKUP = "regulation_lookup"
    REGULATION_CHAT = "regulation_chat"
    SUMMARIZE = "summarize"


@dataclass(frozen=True)
class Message:
    """One turn of a conversation."""

    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Message":
        return cls(role=str(value.get("role", "user")), content=str(value.get("content", "")))


def to_messages(values: Iterable[Any]) -> list[Message]:
    """Accept ``Message`` objects and plain ``{"role", "content"}`` dicts alike."""
    messages: list[Message] = []
    for value in values:
        if isinstance(value, Message):
            messages.append(value)
        elif isinstance(value, Mapping):
            messages.append(Message.from_mapping(value))
        else:  # pragma: no cover - defensive
            raise TypeError(f"Not a chat message: {value!r}")
    return messages


@dataclass(frozen=True)
class Completion:
    """What every provider returns, whatever SDK produced it."""

    text: str
    model: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def created_at_iso(self) -> str:
        return self.created_at.isoformat()


class LLMError(RuntimeError):
    """Base class for every failure the seam reports."""


class LLMUnavailable(LLMError):
    """No usable provider is configured - the feature is off, not broken."""


class LLMRateLimited(LLMError):
    """The upstream vendor refused the call because of rate limits."""


@runtime_checkable
class LLMProvider(Protocol):
    """
    The whole contract an implementation has to satisfy.

    Keep it this small on purpose: anything vendor-specific (token counting,
    streaming, tool calls) belongs behind an implementation, not in the seam.
    """

    name: str

    def is_available(self) -> bool:
        """True when the provider has everything it needs to answer."""

    def complete(
        self,
        messages: Sequence[Any],
        *,
        purpose: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Completion:
        """Answer a chat conversation, or raise an :class:`LLMError`."""
