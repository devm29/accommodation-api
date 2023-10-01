"""The Celery app is imported here so ``@shared_task`` binds to it on startup."""

from .celery import app as celery_app

__all__ = ("celery_app",)
