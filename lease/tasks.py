"""Background work for the lease app."""

from __future__ import annotations

import logging

from celery import shared_task

from .models import Document
from .services import analyze_document_with_gpt

logger = logging.getLogger(__name__)


@shared_task
def analyze_document_task(document_id):
    """
    Review one document out of band.

    ``analyze_document_with_gpt`` already persists a successful review. The
    only thing left here is to make sure a *failed* run does not write
    anything: the return value carries ``status: "Error"``, which is outside
    ``Document.STATUS_CHOICES``, and because ``Document.save()`` mirrors the
    newest document's status onto its lease, storing it would mark the whole
    lease "Error" because OpenAI had a bad minute.
    """
    logger.info("Starting analyze_document_task for document ID: %s.", document_id)
    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        logger.error("analyze_document_task: document %s no longer exists.", document_id)
        return

    try:
        response = analyze_document_with_gpt(document)
    except Exception:
        logger.exception("analyze_document_task failed for document ID: %s.", document_id)
        return

    if response.get("status") in dict(Document.STATUS_CHOICES):
        logger.info("Document %s analysed: %s", document_id, response["status"])
    else:
        logger.error(
            "Document analysis for document ID %s did not produce a verdict: %s",
            document_id,
            response.get("message"),
        )
