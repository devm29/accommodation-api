"""Background work for the regulations app."""

from __future__ import annotations

import logging

from celery import shared_task

from .models import Regulations
from .services import run_analysis

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def analyze_regulation_task(self, regulation_id):
    """Look up one saved search out of band."""
    try:
        regulation = Regulations.objects.get(pk=regulation_id)
    except Regulations.DoesNotExist:
        logger.error("analyze_regulation_task: regulation %s no longer exists.", regulation_id)
        return

    try:
        run_analysis(regulation)
    except Exception:
        logger.exception("analyze_regulation_task failed for regulation %s.", regulation_id)
        Regulations.objects.filter(pk=regulation_id).update(analysis_state=Regulations.STATE_FAILED)
