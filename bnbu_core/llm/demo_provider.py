"""A deterministic provider that never leaves the process.

It exists for two reasons that are worth the file:

* the Docker demo stack boots with seeded, *populated* lease reviews and
  regulation answers without anyone holding an OpenAI account, and
* tests exercise the real parsing, status and persistence code paths instead
  of asserting against a mock's return value.

It is selected explicitly (``LLM_PROVIDER=demo``) and never by accident.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Sequence

from .base import Completion, Message, Purpose, to_messages

_LEASE_ANALYSIS = {
    "verdict": "Draft",
    "confidence": 0.74,
    "summary": (
        "A twelve-month fixed term at $2,450 a month with a two-month security "
        "deposit. Three clauses are worth negotiating before signing: the "
        "blanket subletting ban, an uncapped late fee, and a repair "
        "responsibility that is pushed onto the tenant below $500."
    ),
    "financials": {
        "monthly_rent": 2450,
        "security_deposit": 4900,
        "late_fee": 150,
        "lease_term_months": 12,
    },
    "clauses": [
        {
            "type": "subletting",
            "excerpt": (
                "Tenant shall not sublet the Premises or any portion thereof, "
                "nor assign this Lease, under any circumstance."
            ),
            "risk": "high",
            "finding": (
                "An absolute ban with no landlord-consent carve-out. For a "
                "short-term-rental strategy this clause alone makes the unit "
                "unusable; ask for 'not to be unreasonably withheld' consent."
            ),
            "confidence": 0.93,
        },
        {
            "type": "late_fee",
            "excerpt": "A late charge of $150 plus $25 per day shall accrue.",
            "risk": "medium",
            "finding": (
                "The per-day component is uncapped, so a two-week delay costs "
                "$500. Ask for a cap at 5% of one month's rent."
            ),
            "confidence": 0.88,
        },
        {
            "type": "maintenance",
            "excerpt": "Tenant is responsible for all repairs under $500.",
            "risk": "medium",
            "finding": (
                "Shifts routine appliance and plumbing repair onto the tenant. "
                "Budget roughly $1,200 a year, or negotiate the threshold down."
            ),
            "confidence": 0.81,
        },
        {
            "type": "security_deposit",
            "excerpt": "Security deposit equal to two (2) months' rent.",
            "risk": "low",
            "finding": (
                "Two months is at the legal maximum in several states; check "
                "the local cap and the interest-bearing-account requirement."
            ),
            "confidence": 0.76,
        },
    ],
}

_REGULATION_ANSWER = (
    "SHORT TERM RENTAL ALLOWED WITH RESTRICTIONS\n\n"
    "- Operators must register annually and display the permit number in every "
    "listing.\n"
    "- The unit must be the operator's primary residence for at least 245 days "
    "a year, or hold a legacy non-primary permit.\n"
    "- Occupancy is capped at two guests per bedroom plus two.\n"
    "- Lodging tax is collected by the platform, but the operator files the "
    "annual return.\n\n"
    "Sources: [Municipal code, short-term rentals](https://example.gov/code/str)"
)

_LEASE_CHAT = (
    "The subletting clause is the one to push on. As written it bans "
    "assignment and subletting outright, with no consent mechanism, which "
    "rules out the short-term-rental use you are underwriting. The usual "
    "counter is to accept the ban on assignment but ask for subletting "
    '"with landlord consent, not to be unreasonably withheld", and to name '
    "the platform you intend to list on so the landlord is not surprised "
    "later."
)

_REGULATION_CHAT = (
    "Because the permit is tied to primary residence, the practical route is "
    "either a 245-day owner-occupancy pattern or a legacy non-primary permit "
    "bought with the property. Ask the seller for the permit number and check "
    "whether it transfers - in most of these ordinances it does not."
)

_SUMMARY = (
    "The document sets a twelve-month term, a $2,450 rent, a two-month "
    "deposit, an absolute subletting ban, an uncapped late fee and a $500 "
    "tenant repair threshold."
)

_CANNED: dict[str, str] = {
    Purpose.LEASE_ANALYSIS: json.dumps(_LEASE_ANALYSIS, indent=2),
    Purpose.LEASE_CHAT: _LEASE_CHAT,
    Purpose.REGULATION_LOOKUP: _REGULATION_ANSWER,
    Purpose.REGULATION_CHAT: _REGULATION_CHAT,
    Purpose.SUMMARIZE: _SUMMARY,
}


class DemoProvider:
    """Canned answers, chosen by ``purpose`` and stable for a given input."""

    name = "demo"

    def __init__(self, responses: Optional[dict[str, str]] = None) -> None:
        self.responses = {**_CANNED, **(responses or {})}
        self.calls: list[tuple[str, list[Message]]] = []

    def is_available(self) -> bool:
        return True

    def complete(
        self,
        messages: Sequence[Any],
        *,
        purpose: str = "",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Completion:
        parsed = to_messages(messages)
        self.calls.append((purpose, parsed))
        text = self.responses.get(purpose)
        if text is None:
            digest = hashlib.sha256(
                "".join(message.content for message in parsed).encode("utf-8")
            ).hexdigest()[:12]
            text = f"Demo provider has no canned answer for purpose {purpose!r} ({digest})."
        return Completion(text=text, model=model or "demo-1", provider=self.name)
