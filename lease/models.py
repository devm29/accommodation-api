from django.conf import settings
from django.db import models


class Lease(models.Model):
    STATUS_DRAFT = "Draft"
    STATUS_REJECTED = "Rejected"
    STATUS_APPROVED = "Approved"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_APPROVED, "Approved"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leases"
    )
    date = models.DateField(auto_now_add=True)
    address1 = models.CharField(max_length=255)
    address2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    zip_code = models.CharField(max_length=10)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Every list, search and detail route filters by owner and orders
            # by recency. Without this the primary query is a sequential scan.
            models.Index(fields=["user", "-created_at"], name="lease_user_recent_idx"),
            models.Index(fields=["status"], name="lease_status_idx"),
        ]

    def num_of_docs(self):
        """
        Document count.

        The list endpoint annotates this name onto the queryset, which shadows
        the method and turns N count queries into one join.
        """
        return self.documents.count()

    @property
    def address(self):
        return f"{self.address1}{', ' + self.address2 if self.address2 else ''}"

    def __str__(self):
        return f"{self.address} - {self.city} - {self.status}"


class Document(models.Model):
    STATUS_DRAFT = "Draft"
    STATUS_APPROVED = "Approved"
    STATUS_REJECTED = "Rejected"
    STATUS_PENDING = "Pending"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_PENDING, "Pending"),
    ]

    lease = models.ForeignKey(Lease, related_name="documents", on_delete=models.CASCADE)
    file = models.FileField(upload_to="documents/", blank=True, null=True)
    file_url = models.URLField(blank=True, null=True)
    name = models.CharField(max_length=255)
    version = models.PositiveIntegerField(default=1)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    #: Legacy review payload: ``{status, message, created_time}``.
    gpt_response = models.JSONField(blank=True, null=True)
    #: Structured review: verdict, confidence, financials and clause findings.
    #: See ``lease.analysis.LeaseAnalysis.as_analysis_payload``.
    analysis = models.JSONField(blank=True, null=True)
    chat_history = models.JSONField(default=list, blank=True, null=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["lease", "-version"], name="document_lease_version_idx"),
        ]

    def save(self, *args, **kwargs):
        # Assign the next version number for this lease on first save.
        if not self.pk:
            last_document = Document.objects.filter(lease=self.lease).order_by("-version").first()
            self.version = (last_document.version + 1) if last_document else 1
        super().save(*args, **kwargs)

        # The lease reflects the status of its newest document.
        latest_document = Document.objects.filter(lease=self.lease).order_by("-version").first()
        if latest_document and latest_document.status != self.lease.status:
            self.lease.status = latest_document.status
            self.lease.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return (
            f"Document for Lease {self.lease_id} - {self.name} "
            f"(Version {self.version}) - {self.status}"
        )
