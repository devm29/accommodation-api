"""Where a short-term-rental answer can come from.

The product started with exactly one answer source - ask GPT-4 - and the
prompt, the regex that turned prose into a status, and the database write all
lived in one viewset method. They are three different jobs. This module owns
the first two so the viewset can own none of them, and so a second source (a
curated table today, a municipal-code index or a paid data feed tomorrow) is a
class with one method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence, runtime_checkable

STATUS_ALLOWED = "STR Allowed"
STATUS_RESTRICTED = "STR Allowed with Restrictions"
STATUS_NOT_ALLOWED = "STR Not Allowed"
STATUS_PENDING = "pending"

KNOWN_STATUSES = frozenset({STATUS_ALLOWED, STATUS_RESTRICTED, STATUS_NOT_ALLOWED, STATUS_PENDING})


@dataclass(frozen=True)
class RegulationFinding:
    """One source's answer about one location."""

    status: str
    summary: str
    source: str
    confidence: float = 0.0
    citations: Sequence[str] = field(default_factory=tuple)

    def as_payload(self, created_time: str) -> dict:
        """The JSON stored on ``Regulations.gpt_response``.

        ``status``/``message``/``created_time`` are the keys the existing
        clients read; the rest is additive.
        """
        return {
            "status": self.status,
            "message": self.summary,
            "created_time": created_time,
            "source": self.source,
            "confidence": round(float(self.confidence), 2),
            "citations": list(self.citations),
        }


class RegulationSourceError(RuntimeError):
    """The source was asked and could not answer."""


@runtime_checkable
class RegulationSource(Protocol):
    name: str
    #: True when the source answers locally and may be consulted inside a
    #: request/response cycle.
    is_offline: bool

    def is_available(self) -> bool:
        """True when the source can be consulted at all."""

    def lookup(self, query: str) -> Optional[RegulationFinding]:
        """Answer for ``query``, or ``None`` when this source does not know."""
