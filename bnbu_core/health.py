"""Liveness and readiness endpoints.

``/api/health/`` answers without touching anything, so a container that is
serving requests at all reports healthy. ``/api/ready/`` checks the two
dependencies the API cannot work without and reports the optional integrations
separately - the product is designed to run with none of them configured, so
a missing OpenAI key is information, not a failure.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from bnbu_core.llm import get_llm_provider
from bnbu_core.regulation_sources import configured_chain

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([AllowAny])
def ready(request):
    checks = {"database": _check_database(), "cache": _check_cache()}
    provider = get_llm_provider()
    integrations = {
        "llm_provider": provider.name,
        "llm_available": provider.is_available(),
        "regulation_sources": list(configured_chain()),
        "airdna_configured": bool(settings.AIRDNA_URL and settings.AIRDNA_API_KEY),
        "cloudinary_configured": bool(settings.CLOUDINARY_CONFIGURED),
    }
    ok = all(checks.values())
    return Response(
        {"status": "ready" if ok else "degraded", "checks": checks, "integrations": integrations},
        status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("Readiness: the database is not reachable.")
        return False
    return True


def _check_cache() -> bool:
    try:
        cache.set("bnbu:readiness", "1", 10)
        return cache.get("bnbu:readiness") == "1"
    except Exception:
        logger.exception("Readiness: the cache is not reachable.")
        return False
