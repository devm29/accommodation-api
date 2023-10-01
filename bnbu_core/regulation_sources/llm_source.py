"""The language-model regulation source.

This is the prompt and the prose-to-status classification that used to sit in
``RegulationsViewSet._analyze_location_with_gpt``.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bnbu_core.llm import LLMError, Message, Purpose, get_llm_provider

from .base import (
    STATUS_ALLOWED,
    STATUS_NOT_ALLOWED,
    STATUS_PENDING,
    STATUS_RESTRICTED,
    RegulationFinding,
    RegulationSourceError,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a consultant specialising in short-term rental (STR) law. Answer
concisely and accurately about the legality of short-term rentals in a
specific place.

Begin every answer with exactly one of these lines:

    SHORT TERM RENTAL ALLOWED
    SHORT TERM RENTAL ALLOWED WITH RESTRICTIONS
    SHORT TERM RENTAL NOT ALLOWED

Then give bullet-point summaries of the relevant rules. Cite the specific code
or ordinance section (for example "LUC 20.20.800") wherever you rely on one,
and format every URL as Markdown so it is clickable, naming the source site.

Be explicit about whom a rule binds - owner, authorised agent, tenant or
representative - and about residency or occupancy thresholds, quoting the
exact number of days where one applies. Where short-term rentals are banned or
restricted, say so plainly and then set out any lawful alternatives: different
permit classes, unincorporated areas, zoning reclassification, or
non-primary-residence allowances.

Never invent a citation. If you are unsure, say what is uncertain and what the
reader should check.
"""

# Ordered: the restricted phrasing contains the allowed phrasing, so it has to
# be tested first or every restricted answer reads as a plain allow.
_STATUS_PATTERNS = (
    (STATUS_RESTRICTED, re.compile(r"\bSHORT TERM RENTAL ALLOWED WITH RESTRICTIONS\b", re.I)),
    (STATUS_ALLOWED, re.compile(r"\bSHORT TERM RENTAL ALLOWED\b", re.I)),
    (STATUS_NOT_ALLOWED, re.compile(r"\bSHORT TERM RENTAL NOT ALLOWED\b", re.I)),
)

_CITATION_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def classify(text: str) -> str:
    """Map an answer onto a stored status, or ``pending`` when it is unclear."""
    for status, pattern in _STATUS_PATTERNS:
        if pattern.search(text or ""):
            return status
    return STATUS_PENDING


def extract_citations(text: str, limit: int = 10) -> tuple[str, ...]:
    return tuple(f"{label} ({url})" for label, url in _CITATION_PATTERN.findall(text or ""))[:limit]


class LLMRegulationSource:
    """Ask the configured language model."""

    name = "llm"
    #: Calls a vendor over the network; belongs in a worker, not a request.
    is_offline = False

    def __init__(self, provider=None, temperature: float = 0.2) -> None:
        self._provider = provider
        self.temperature = temperature

    @property
    def provider(self):
        return self._provider or get_llm_provider()

    def is_available(self) -> bool:
        return self.provider.is_available()

    def lookup(self, query: str) -> Optional[RegulationFinding]:
        if not (query or "").strip():
            return None

        messages = [
            Message("system", SYSTEM_PROMPT),
            Message(
                "user",
                "Analyse the legality of short-term rentals for the following "
                f"location or address:\n- Search: {query}",
            ),
        ]
        try:
            completion = self.provider.complete(
                messages, purpose=Purpose.REGULATION_LOOKUP, temperature=self.temperature
            )
        except LLMError as exc:
            raise RegulationSourceError(str(exc)) from exc

        text = completion.text.strip()
        status = classify(text)
        return RegulationFinding(
            status=status,
            summary=text,
            source=f"{self.name}:{completion.provider or self.provider.name}",
            # A model answer that did not even state its own verdict line is
            # not worth the same as one that did.
            confidence=0.7 if status != STATUS_PENDING else 0.2,
            citations=extract_citations(text),
        )
