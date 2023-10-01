"""Serializers for the lease app.

These validate and render. Storage uploads and model calls live in
``lease.services``; a serializer that uploads to a third party is a serializer
you cannot unit-test.
"""

from rest_framework import serializers

from .models import Document, Lease


class DocumentSerializer(serializers.ModelSerializer):
    """The full document, used on the document routes."""

    lease_id = serializers.PrimaryKeyRelatedField(source="lease", read_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "lease_id",
            "name",
            "file",
            "version",
            "uploaded_at",
            "status",
            "gpt_response",
            "analysis",
            "chat_history",
        ]


class DocumentSummarySerializer(serializers.ModelSerializer):
    """
    The document as it appears nested inside a lease.

    A lease list used to embed every document's full review text and entire
    chat transcript, so one page of ten leases could carry several megabytes
    of conversation nobody on that screen reads.
    """

    lease_id = serializers.PrimaryKeyRelatedField(source="lease", read_only=True)

    class Meta:
        model = Document
        fields = ["id", "lease_id", "name", "file", "file_url", "version", "uploaded_at", "status"]


class LeaseSerializer(serializers.ModelSerializer):
    num_of_docs = serializers.IntegerField(read_only=True)
    documents = DocumentSummarySerializer(many=True, read_only=True)

    class Meta:
        model = Lease
        fields = [
            "id",
            "date",
            "address1",
            "address2",
            "city",
            "state",
            "zip_code",
            "status",
            "num_of_docs",
            "documents",
        ]


class LeaseUploadSerializer(serializers.ModelSerializer):
    documents = serializers.ListField(
        child=serializers.FileField(), write_only=True, required=False
    )

    class Meta:
        model = Lease
        fields = ["address1", "address2", "city", "state", "zip_code", "status", "documents"]


class RevisedLeaseUploadSerializer(serializers.Serializer):
    documents = serializers.ListField(
        child=serializers.FileField(), write_only=True, required=False
    )


class GPTChatSerializer(serializers.Serializer):
    """
    The chat request body.

    ``document_id`` is redundant with the id already in the URL, and the view
    used to trust the body and ignore the URL entirely. It is kept optional so
    the existing frontend keeps working, but the URL is now authoritative and a
    body that disagrees with it is rejected rather than silently preferred.
    """

    message = serializers.CharField(
        required=True,
        allow_blank=False,
        help_text="Your question about the reviewed document.",
    )
    document_id = serializers.IntegerField(
        required=False,
        help_text="Optional and deprecated: must match the id in the URL.",
    )


class ClauseFindingSerializer(serializers.Serializer):
    """Documents the structured review's shape for the generated schema."""

    type = serializers.CharField()
    excerpt = serializers.CharField()
    risk = serializers.ChoiceField(choices=["low", "medium", "high"])
    finding = serializers.CharField()
    confidence = serializers.FloatField(min_value=0, max_value=1)


class LeaseAnalysisSerializer(serializers.Serializer):
    """The payload returned by ``GET /api/documents/<pk>/analysis/``."""

    verdict = serializers.ChoiceField(choices=["Approved", "Rejected", "Draft"])
    confidence = serializers.FloatField(min_value=0, max_value=1)
    summary = serializers.CharField()
    financials = serializers.DictField()
    clauses = ClauseFindingSerializer(many=True)
    model = serializers.CharField()
    provider = serializers.CharField()
    structured = serializers.BooleanField()
    pages = serializers.IntegerField()
    created_time = serializers.CharField()
