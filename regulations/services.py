"""
Regulation use cases.

Creating a regulation used to call GPT-4 inline, inside ``perform_create``,
inside the POST. That one line was the product's worst scalability problem:
a request that holds a worker for however long the model takes, with a
gunicorn worker count in the low single digits.

It now does three things instead. A repeat search is answered from Redis
immediately. A location the curated table covers is answered from the table
immediately. Anything else is queued, the row comes back with
``analysis_state: "pending"``, and the client polls the row it already has.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from django.conf import settings
from django.core.cache import cache

from bnbu_core.json_utils import as_mapping
from bnbu_core.llm import LLMError, LLMUnavailable, Message, Purpose, get_llm_provider
from bnbu_core.regulation_sources import (
    STATUS_PENDING,
    RegulationFinding,
    RegulationSourceChain,
    RegulationSourceError,
)
from bnbu_core.regulation_sources.static_source import normalise

from .models import Regulations

logger = logging.getLogger(__name__)

CACHE_PREFIX = "bnbu:regulation:v1:"

CHAT_SYSTEM_PROMPT = """\
You are a consultant specialising in short-term rental (STR) law, continuing a
conversation about one location that has already been researched.

Answer from the analysis below. Cite the code or ordinance section behind each
claim, format URLs as Markdown, and be explicit about whom a rule binds -
owner, authorised agent, tenant or representative. Where the rules are
restrictive, set out the lawful alternatives rather than stopping at "no".
Never invent a citation.

Analysis of this location:
{summary}
"""

CHAT_HISTORY_TURNS = 20


class RegulationServiceError(RuntimeError):
    """A regulation use case could not be completed."""


def cache_key(search: str) -> str:
    """
    Cache key for one location.

    Normalised, so "Kirkland, WA" and "kirkland wa" share an answer, and
    hashed, so a long address cannot blow the key-length limit.
    """
    digest = hashlib.sha256(normalise(search).encode("utf-8")).hexdigest()
    return f"{CACHE_PREFIX}{digest}"


def clear_cached_answer(search: str) -> None:
    """Forget the cached answer for one location, so the next lookup is fresh."""
    cache.delete(cache_key(search))


def cache_ttl() -> int:
    return int(getattr(settings, "REGULATION_CACHE_TTL", 86400) or 86400)


def create_regulation(*, user, search: str, chain: Optional[RegulationSourceChain] = None):
    """
    Save the search, then answer it as cheaply as possible.

    Returns the row. ``analysis_state`` tells the caller whether an answer is
    already attached or a worker is on its way to it.
    """
    regulation = Regulations.objects.create(
        user=user, search=search, status=STATUS_PENDING, analysis_state=Regulations.STATE_PENDING
    )

    cached = cache.get(cache_key(search))
    if cached:
        logger.info("Regulation %s answered from cache.", regulation.pk)
        apply_payload(regulation, cached)
        return regulation

    # Only the free, local sources are allowed to answer inline; anything that
    # makes a network call is queued.
    finding = _try_offline_sources(search, chain=chain)
    if finding is not None:
        apply_finding(regulation, finding)
        return regulation

    _enqueue(regulation.pk)
    return regulation


def run_analysis(regulation: Regulations, *, chain: Optional[RegulationSourceChain] = None) -> dict:
    """Consult the source chain and store the result. Used by the Celery task."""
    Regulations.objects.filter(pk=regulation.pk).update(analysis_state=Regulations.STATE_RUNNING)
    regulation.analysis_state = Regulations.STATE_RUNNING

    chain = chain or RegulationSourceChain()
    try:
        finding = chain.lookup(regulation.search)
    except RegulationSourceError as exc:
        return _record_failure(regulation, str(exc))

    if finding is None:
        return _record_failure(
            regulation, "No configured regulation source could answer that search."
        )
    return apply_finding(regulation, finding)


def apply_finding(regulation: Regulations, finding: RegulationFinding) -> dict:
    """Store a finding on the row and cache it for the next person who asks."""
    payload = finding.as_payload(datetime.now(timezone.utc).isoformat())
    cache.set(cache_key(regulation.search), payload, cache_ttl())
    return apply_payload(regulation, payload)


def apply_payload(regulation: Regulations, payload: dict) -> dict:
    """Write an answer payload onto the row."""
    payload = dict(payload)
    status = payload.get("status")
    if status in dict(Regulations.STATUS_CHOICES):
        regulation.status = status
    else:
        logger.error(
            "Regulation analysis for %s produced no verdict: %s",
            regulation.pk,
            payload.get("message"),
        )
        regulation.status = STATUS_PENDING

    regulation.gpt_response = payload
    regulation.source = str(payload.get("source", ""))[:100]
    regulation.analysis_state = Regulations.STATE_COMPLETE
    regulation.save(update_fields=["status", "gpt_response", "source", "analysis_state"])
    return payload


def chat(*, regulation: Regulations, message: str, provider=None) -> dict:
    """Append one exchange about a researched location to its chat history."""
    provider = provider or get_llm_provider()
    summary = analysis_summary(regulation)

    history = list(regulation.chat_history or [])
    history.append(_turn("user", message))

    conversation = [Message("system", CHAT_SYSTEM_PROMPT.format(summary=summary))]
    conversation += [
        Message(str(turn.get("role", "user")), str(turn.get("content", "")))
        for turn in history[-CHAT_HISTORY_TURNS:]
    ]

    try:
        completion = provider.complete(conversation, purpose=Purpose.REGULATION_CHAT)
    except LLMUnavailable as exc:
        raise RegulationServiceError(str(exc)) from exc
    except LLMError as exc:
        raise RegulationServiceError(f"The language model could not answer: {exc}") from exc

    history.append(_turn("assistant", completion.text))
    regulation.chat_history = history
    regulation.save(update_fields=["chat_history"])

    return {"response": completion.text, "chat_history": history, "summary": summary}


def analysis_summary(regulation: Regulations) -> str:
    return str(as_mapping(regulation.gpt_response).get("message") or "No summary available.")


def chat_history_payload(regulation: Regulations) -> dict:
    payload = as_mapping(regulation.gpt_response)
    return {
        "gpt_response": {
            "message": payload.get("message"),
            "status": payload.get("status"),
            "timestamp": payload.get("created_time"),
        },
        "chat_history": regulation.chat_history or [],
        "analysis_state": regulation.analysis_state,
        "source": regulation.source,
    }


def _try_offline_sources(search: str, *, chain: Optional[RegulationSourceChain]):
    """Ask only the sources that cost nothing and cannot block."""
    if chain is not None:
        sources = [source for source in chain.sources if _is_offline(source)]
    else:
        sources = [source for source in RegulationSourceChain().sources if _is_offline(source)]

    for source in sources:
        if not source.is_available():
            continue
        try:
            finding = source.lookup(search)
        except RegulationSourceError as exc:
            logger.warning("Offline source %s failed: %s", source.name, exc)
            continue
        if finding is not None:
            return finding
    return None


def _is_offline(source) -> bool:
    return bool(getattr(source, "is_offline", False))


def _enqueue(regulation_id: int) -> None:
    from .tasks import analyze_regulation_task

    analyze_regulation_task.delay(regulation_id)


def _record_failure(regulation: Regulations, message: str) -> dict:
    payload = {
        "status": "error",
        "message": message,
        "created_time": datetime.now(timezone.utc).isoformat(),
    }
    regulation.gpt_response = payload
    regulation.analysis_state = Regulations.STATE_FAILED
    regulation.save(update_fields=["gpt_response", "analysis_state"])
    logger.error("Regulation %s could not be analysed: %s", regulation.pk, message)
    return payload


def _turn(role: str, content: str) -> dict:
    return {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
