"""
Lease and document use cases.

Viewsets in this app parse a request and render a response. Everything between
those two jobs - uploading to storage, queueing a review, running the review,
appending a chat turn - lives here, so it is testable without a request and
callable from a Celery task.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from django.db import transaction

from bnbu_core.json_utils import as_mapping
from bnbu_core.llm import LLMError, LLMUnavailable, Message, Purpose, get_llm_provider

from .analysis import LeaseAnalysis, LeaseAnalysisError, analyse_document
from .models import Document, Lease
from .storage import DocumentStorageError, store_document

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """\
You are a short-term-rental contract expert helping a tenant understand a
lease that has already been reviewed. Answer from the review below and the
conversation so far.

Be concrete: name the clause, say what it costs or prevents, and say what to
ask for instead. If the review does not cover what was asked, say so rather
than inferring terms the document may not contain.

Review of this document:
{summary}
"""

#: How much of a chat history is replayed to the model. An unbounded history
#: grows the prompt - and the bill - without bound on a long conversation.
CHAT_HISTORY_TURNS = 20


class LeaseServiceError(RuntimeError):
    """A lease use case could not be completed."""


# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------


@transaction.atomic
def create_lease(*, user, address: dict, files: Iterable = ()) -> Lease:
    """Create a lease and attach the uploaded documents to it."""
    lease = Lease.objects.create(user=user, **address)
    attach_documents(lease=lease, files=files)
    return lease


def attach_documents(*, lease: Lease, files: Iterable = ()) -> list[int]:
    """Store each file and record it as the next version of the lease."""
    document_ids: list[int] = []
    for uploaded in files:
        try:
            url = store_document(uploaded)
        except DocumentStorageError as exc:
            raise LeaseServiceError(str(exc)) from exc
        document = Document.objects.create(lease=lease, file_url=url, name=uploaded.name)
        document_ids.append(document.id)
    return document_ids


# --------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------


def queue_review(documents: Iterable[Document]) -> list[dict]:
    """Queue a GPT review for each document and describe what happened."""
    # Imported here rather than at module scope: ``lease.tasks`` imports this
    # module, and the task is only needed when something is actually queued.
    from .tasks import analyze_document_task

    results = []
    for document in documents:
        if not document.file_url:
            results.append({"document_id": document.id, "detail": "Document file not found."})
            continue
        if document.status != Document.STATUS_PENDING:
            document.status = Document.STATUS_PENDING
            document.save(update_fields=["status"])
        analyze_document_task.delay(document.id)
        results.append(
            {
                "document_id": document.id,
                "detail": (
                    f"Document review for document ID {document.id} has started. "
                    "The review process is in progress."
                ),
            }
        )
    return results


def review_document(document: Document, *, provider=None) -> LeaseAnalysis:
    """
    Run the review and persist it.

    Raises :class:`LeaseAnalysisError` on failure; the caller decides whether
    that is a 500, a retry or a log line. It deliberately does *not* write a
    failure into ``Document.status`` - "we could not reach OpenAI" is not a
    verdict on a lease.
    """
    analysis = analyse_document(document, provider=provider)
    document.status = analysis.verdict
    document.analysis = analysis.as_analysis_payload()
    document.gpt_response = analysis.as_gpt_response()
    document.save(update_fields=["status", "analysis", "gpt_response"])
    return analysis


def analyze_document_with_gpt(document) -> dict:
    """
    Backwards-compatible entry point used by the Celery task.

    Returns the legacy ``{status, message, created_time}`` payload, or a
    ``status: "Error"`` mapping - a value deliberately outside
    ``Document.STATUS_CHOICES``, so the task can tell a verdict from a failure.
    """
    try:
        analysis = review_document(document)
    except (LeaseAnalysisError, LLMError) as exc:
        logger.warning("Lease analysis failed for document %s: %s", document.pk, exc)
        return {"status": "Error", "message": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected failure analysing document %s.", document.pk)
        return {"status": "Error", "message": f"An unexpected error occurred: {exc}"}
    return analysis.as_gpt_response()


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------


def chat_about_document(*, document: Document, message: str, provider=None) -> dict:
    """Append one exchange about a reviewed document to its chat history."""
    provider = provider or get_llm_provider()
    summary = review_summary(document)

    history = list(document.chat_history or [])
    history.append(_turn("user", message))

    conversation = [Message("system", CHAT_SYSTEM_PROMPT.format(summary=summary))]
    conversation += [
        Message(str(turn.get("role", "user")), str(turn.get("content", "")))
        for turn in history[-CHAT_HISTORY_TURNS:]
    ]

    try:
        completion = provider.complete(conversation, purpose=Purpose.LEASE_CHAT)
    except LLMUnavailable as exc:
        raise LeaseServiceError(str(exc)) from exc
    except LLMError as exc:
        raise LeaseServiceError(f"The language model could not answer: {exc}") from exc

    history.append(_turn("assistant", completion.text))
    document.chat_history = history
    document.save(update_fields=["chat_history"])

    return {"response": completion.text, "chat_history": history, "summary": summary}


def review_summary(document: Document) -> str:
    """The reviewed summary a chat should answer from."""
    analysis = as_mapping(document.analysis)
    if analysis.get("summary"):
        return str(analysis["summary"])
    legacy = as_mapping(document.gpt_response)
    return str(legacy.get("message") or "No summary available. Please review the document first.")


def chat_history_payload(document: Document) -> dict:
    """Everything the detail screen needs about one document's review and chat."""
    legacy = as_mapping(document.gpt_response)
    return {
        "document_uploaded_at": str(document.uploaded_at),
        "gpt_response": {
            "message": legacy.get("message"),
            "status": legacy.get("status"),
            "timestamp": legacy.get("created_time"),
        },
        "chat_history": document.chat_history or [],
    }


def analysis_payload(document: Document) -> Optional[dict]:
    """The structured review, or ``None`` when the document has not been reviewed."""
    analysis = as_mapping(document.analysis)
    return analysis or None


def _turn(role: str, content: str) -> dict:
    return {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
