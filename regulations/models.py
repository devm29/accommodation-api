from django.conf import settings
from django.db import models

from bnbu_core.regulation_sources import (
    STATUS_ALLOWED,
    STATUS_NOT_ALLOWED,
    STATUS_PENDING,
    STATUS_RESTRICTED,
)


class Regulations(models.Model):
    """One saved "can I run a short-term rental here?" question and its answer."""

    STATUS_CHOICES = [
        (STATUS_ALLOWED, "STR Allowed"),
        (STATUS_NOT_ALLOWED, "STR Not Allowed"),
        (STATUS_RESTRICTED, "STR Allowed with Restrictions"),
        ("STR Pending Approval", "STR Pending Approval"),
        # The default has always been 'pending'; it was simply never one of
        # the choices, so the column's own default failed model validation.
        (STATUS_PENDING, "Pending"),
    ]

    STATE_PENDING = "pending"
    STATE_RUNNING = "running"
    STATE_COMPLETE = "complete"
    STATE_FAILED = "failed"
    ANALYSIS_STATES = [
        (STATE_PENDING, "Queued"),
        (STATE_RUNNING, "Running"),
        (STATE_COMPLETE, "Complete"),
        (STATE_FAILED, "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="regulations"
    )
    date = models.DateField(auto_now_add=True)
    search = models.CharField(max_length=255)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    gpt_response = models.JSONField(blank=True, null=True)
    chat_history = models.JSONField(default=list, blank=True, null=True)
    #: Where the answer is in its lifecycle. The lookup runs out of band, so a
    #: row exists - and is returned to the client - before it has an answer.
    analysis_state = models.CharField(max_length=20, choices=ANALYSIS_STATES, default=STATE_PENDING)
    #: Which source answered: the curated table, the model, or nothing yet.
    source = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "regulations"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="regulation_user_recent_idx"),
            models.Index(fields=["status"], name="regulation_status_idx"),
        ]

    def __str__(self):
        return f"{self.search} - {self.status}"
