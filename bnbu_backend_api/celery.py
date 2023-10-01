"""Celery application.

Broker and result-backend URLs come from Django settings under the ``CELERY_``
namespace, which ``config_from_object`` reads lazily - nothing here may touch
``django.conf.settings`` at import time, because this module is imported from
the package ``__init__`` while the settings module itself is still loading.
"""

from __future__ import absolute_import, unicode_literals

import os
import ssl

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bnbu_backend_api.settings")

app = Celery("bnbu_backend_api")
app.config_from_object("django.conf:settings", namespace="CELERY")

_USE_SSL = os.getenv("REDIS_URL", "redis://localhost:6379/0").startswith("rediss://")
_SSL_OPTIONS = {"ssl_cert_reqs": ssl.CERT_NONE} if _USE_SSL else None

app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    broker_use_ssl=_SSL_OPTIONS,
    redis_backend_use_ssl=_SSL_OPTIONS,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "socket_timeout": 30,
        "socket_connect_timeout": 30,
        "max_connections": 10,
    },
    # A review can take minutes. Acknowledging late, and prefetching one task
    # at a time, means a worker that dies mid-review returns the job to the
    # queue instead of losing it.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=int(os.getenv("CELERY_SOFT_TIME_LIMIT", "540")),
    task_time_limit=int(os.getenv("CELERY_TIME_LIMIT", "600")),
)

app.autodiscover_tasks()
