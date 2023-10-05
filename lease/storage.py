"""Where lease documents are kept.

Cloudinary is optional: the API boots and serves every other endpoint without
it. Wrapping the upload here means the failure is one catchable exception at
one call site rather than a vendor traceback out of a serializer.
"""

from __future__ import annotations

import logging

from cloudinary.uploader import upload
from django.conf import settings

logger = logging.getLogger(__name__)


class DocumentStorageError(RuntimeError):
    """The document could not be stored."""


def is_configured() -> bool:
    return bool(settings.CLOUDINARY_CONFIGURED)


def store_document(uploaded_file) -> str:
    """Upload one file and return the URL it can be fetched from."""
    if not is_configured():
        raise DocumentStorageError(
            "Document storage is not configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
        )
    try:
        result = upload(uploaded_file, use_filename=True, resource_type="raw", access_mode="public")
    except Exception as exc:
        logger.exception("Upload of %s failed.", getattr(uploaded_file, "name", "?"))
        raise DocumentStorageError(str(exc)) from exc

    url = result.get("secure_url") or result.get("url")
    if not url:
        raise DocumentStorageError("The storage backend returned no URL for the upload.")
    return url
