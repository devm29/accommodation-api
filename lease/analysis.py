"""
Structured lease review.

The old pipeline asked GPT-4 for prose and then decided the verdict with
``if "approved" in text.lower()`` - which reads "this lease would not be
approved" as an approval, and gives a reviewer no way to see *which* clause
drove the answer. This module asks for a typed result instead: a verdict with
a confidence, the money, and one entry per clause that matters, each with the
text it came from.

Everything vendor-specific lives behind :mod:`bnbu_core.llm`, so this module
is about leases, not about OpenAI.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import PyPDF2
import requests
from django.conf import settings

from bnbu_core.json_utils import first_json_object
from bnbu_core.llm import Completion, LLMError, Message, Purpose, get_llm_provider

logger = logging.getLogger(__name__)

VERDICT_APPROVED = "Approved"
VERDICT_REJECTED = "Rejected"
VERDICT_DRAFT = "Draft"
VERDICTS = (VERDICT_APPROVED, VERDICT_REJECTED, VERDICT_DRAFT)

RISK_LEVELS = ("low", "medium", "high")

PAGES_PER_CHUNK = 5

SYSTEM_PROMPT = """\
You are a short-term-rental contract expert reviewing a residential lease for
a prospective tenant or operator. Identify unusual clauses, legal risk, and
anything that would stop the property being let short-term.

Reply with a single JSON object and nothing else, in exactly this shape:

{
  "verdict": "Approved" | "Rejected" | "Draft",
  "confidence": 0.0-1.0,
  "summary": "two or three sentences",
  "financials": {
    "monthly_rent": number or null,
    "security_deposit": number or null,
    "late_fee": number or null,
    "lease_term_months": number or null
  },
  "clauses": [
    {
      "type": "subletting" | "late_fee" | "security_deposit" | "maintenance" |
              "termination" | "assignment" | "occupancy" | "utilities" |
              "insurance" | "other",
      "excerpt": "the lease's own words, quoted, at most 300 characters",
      "risk": "low" | "medium" | "high",
      "finding": "what it means for the tenant and what to negotiate",
      "confidence": 0.0-1.0
    }
  ]
}

Use "Approved" when nothing material stands against the tenant, "Rejected"
when there is a clause that should stop the deal, and "Draft" when the lease
is negotiable or incomplete. Quote excerpts verbatim; never invent one. Set a
clause's confidence below 0.5 when the wording is ambiguous, and omit the
clause entirely rather than guessing at its text.
"""

SUMMARISE_PROMPT = (
    "Summarise this section of a residential lease. Keep every number, every "
    "deadline, and the exact wording of any clause about subletting, "
    "assignment, fees, deposits, termination or repairs."
)

_NEGATED_APPROVAL = re.compile(
    r"\b(not|never|cannot|can't|shouldn't|should not)\s+(be\s+)?approv", re.I
)
_APPROVAL = re.compile(r"\bapproved?\b", re.I)
_REJECTION = re.compile(r"\breject(ed|ion)?\b", re.I)


class LeaseAnalysisError(RuntimeError):
    """The document could not be analysed."""


@dataclass(frozen=True)
class ClauseFinding:
    type: str
    excerpt: str
    risk: str
    finding: str
    confidence: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LeaseAnalysis:
    verdict: str
    confidence: float
    summary: str
    clauses: Sequence[ClauseFinding] = field(default_factory=tuple)
    financials: dict = field(default_factory=dict)
    model: str = ""
    provider: str = ""
    structured: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pages: int = 0

    def as_analysis_payload(self) -> dict:
        """What is stored on ``Document.analysis``."""
        return {
            "verdict": self.verdict,
            "confidence": round(float(self.confidence), 2),
            "summary": self.summary,
            "financials": dict(self.financials),
            "clauses": [clause.as_dict() for clause in self.clauses],
            "model": self.model,
            "provider": self.provider,
            "structured": self.structured,
            "pages": self.pages,
            "created_time": self.created_at.isoformat(),
        }

    def as_gpt_response(self) -> dict:
        """
        The legacy ``gpt_response`` shape.

        Existing clients read ``status``, ``message`` and ``created_time``;
        they keep working unchanged while the structured result lands beside
        them.
        """
        return {
            "status": self.verdict,
            "message": self.summary,
            "created_time": self.created_at.isoformat(),
        }


def analyse_document(document, *, provider=None) -> LeaseAnalysis:
    """Download, read and review one lease document."""
    provider = provider or get_llm_provider()
    text_chunks, pages = extract_text_chunks(document)

    max_chunks = int(getattr(settings, "LEASE_MAX_CHUNKS", 12) or 12)
    if len(text_chunks) > max_chunks:
        # One upload should not be able to fan out into an unbounded number of
        # paid calls. Review the front of the document, which is where the
        # terms are, and say so in the summary.
        logger.warning(
            "Document %s has %d chunks; reviewing the first %d.",
            getattr(document, "id", "?"),
            len(text_chunks),
            max_chunks,
        )
        text_chunks = text_chunks[:max_chunks]

    combined = _condense(text_chunks, provider)

    try:
        completion = provider.complete(
            [Message("system", SYSTEM_PROMPT), Message("user", combined)],
            purpose=Purpose.LEASE_ANALYSIS,
            temperature=0,
        )
    except LLMError as exc:
        raise LeaseAnalysisError(str(exc)) from exc

    return parse_completion(completion, pages=pages)


def extract_text_chunks(document) -> tuple[list[str], int]:
    """Fetch the PDF behind ``document`` and return its text, five pages at a time."""
    file_url = getattr(document, "file_url", None)
    if not file_url:
        raise LeaseAnalysisError("Document file URL is not set.")

    try:
        response = requests.get(file_url, stream=True, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LeaseAnalysisError(f"Could not fetch the document: {exc}") from exc

    try:
        reader = PyPDF2.PdfReader(io.BytesIO(response.content))
        pages = len(reader.pages)
        chunks = [
            "".join(
                # extract_text() returns None for pages with no extractable
                # text (scans, pure images), which made str.join raise.
                reader.pages[index].extract_text() or ""
                for index in range(start, min(start + PAGES_PER_CHUNK, pages))
            )
            for start in range(0, pages, PAGES_PER_CHUNK)
        ]
    except Exception as exc:
        raise LeaseAnalysisError(f"Could not read the PDF: {exc}") from exc

    chunks = [chunk for chunk in chunks if chunk.strip()]
    if not chunks:
        raise LeaseAnalysisError(
            "No extractable text in the document - it may be a scan rather than a PDF of text."
        )
    return chunks, pages


def _condense(chunks: Sequence[str], provider) -> str:
    """Summarise chunk by chunk when the document is long enough to need it."""
    if len(chunks) <= 1:
        return chunks[0] if chunks else ""

    summaries: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        try:
            completion = provider.complete(
                [Message("system", SUMMARISE_PROMPT), Message("user", chunk)],
                purpose=Purpose.SUMMARIZE,
                temperature=0,
            )
            summaries.append(completion.text)
        except LLMError as exc:
            # One failed section should not lose the other nineteen.
            logger.warning("Could not summarise chunk %d: %s", index, exc)
    if not summaries:
        raise LeaseAnalysisError("Every section of the document failed to summarise.")
    return "\n\n".join(summaries)


def parse_completion(completion: Completion, *, pages: int = 0) -> LeaseAnalysis:
    """Turn a model answer into a :class:`LeaseAnalysis`, structured or not."""
    payload = first_json_object(completion.text)
    if payload:
        return _from_payload(payload, completion, pages=pages)

    logger.warning("Lease analysis did not return JSON; falling back to keyword reading.")
    verdict = verdict_from_prose(completion.text)
    return LeaseAnalysis(
        verdict=verdict,
        # A verdict inferred from prose is a guess, and it is recorded as one.
        confidence=0.3,
        summary=completion.text.strip(),
        clauses=(),
        financials={},
        model=completion.model,
        provider=completion.provider,
        structured=False,
        created_at=completion.created_at,
        pages=pages,
    )


def _from_payload(payload: dict, completion: Completion, *, pages: int) -> LeaseAnalysis:
    verdict = str(payload.get("verdict", "")).strip().title()
    if verdict not in VERDICTS:
        verdict = VERDICT_DRAFT

    clauses = tuple(
        clause
        for clause in (_clause_from(entry) for entry in payload.get("clauses", []) or [])
        if clause is not None
    )

    return LeaseAnalysis(
        verdict=verdict,
        confidence=_ratio(payload.get("confidence"), default=0.5),
        summary=str(payload.get("summary", "")).strip(),
        clauses=clauses,
        financials=_financials(payload.get("financials")),
        model=completion.model,
        provider=completion.provider,
        structured=True,
        created_at=completion.created_at,
        pages=pages,
    )


def _clause_from(entry: Any) -> Optional[ClauseFinding]:
    if not isinstance(entry, dict):
        return None
    excerpt = str(entry.get("excerpt", "")).strip()
    finding = str(entry.get("finding", "")).strip()
    if not (excerpt or finding):
        return None
    risk = str(entry.get("risk", "")).strip().lower()
    return ClauseFinding(
        type=str(entry.get("type", "other")).strip().lower() or "other",
        excerpt=excerpt[:300],
        risk=risk if risk in RISK_LEVELS else "medium",
        finding=finding,
        confidence=_ratio(entry.get("confidence"), default=0.5),
    )


def _financials(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Optional[float]] = {}
    for key in ("monthly_rent", "security_deposit", "late_fee", "lease_term_months"):
        raw = value.get(key)
        if raw is None or raw == "":
            out[key] = None
            continue
        try:
            out[key] = float(str(raw).replace("$", "").replace(",", "").strip())
        except (TypeError, ValueError):
            out[key] = None
    return out


def _ratio(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def verdict_from_prose(text: str) -> str:
    """
    Read a verdict out of unstructured prose.

    Only used when the model ignored the JSON instruction. The negation check
    is the whole point: the previous implementation was a bare substring test,
    so "this lease would not be approved" came back as ``Approved``.
    """
    body = text or ""
    if _NEGATED_APPROVAL.search(body):
        return VERDICT_REJECTED
    if _REJECTION.search(body):
        return VERDICT_REJECTED
    if _APPROVAL.search(body):
        return VERDICT_APPROVED
    return VERDICT_DRAFT
