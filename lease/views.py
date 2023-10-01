"""
HTTP surface for the lease app.

Every action here does three things and no more: read the request, call a
function in :mod:`lease.services`, and turn the result into a response. The
uploading, the queueing, the prompt and the persistence all live behind that
call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bnbu_core.mixins import OwnerScopedQuerysetMixin, resolve_detail_pk
from bnbu_core.pagination import DefaultPagination
from bnbu_core.permissions import IsAdminOrOwnData, IsSpecificUserType

from . import services
from .models import Document, Lease
from .serializers import (
    DocumentSerializer,
    GPTChatSerializer,
    LeaseSerializer,
    LeaseUploadSerializer,
    RevisedLeaseUploadSerializer,
)

logger = logging.getLogger(__name__)


class LeaseViewSet(OwnerScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Lease.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    search_fields = ["address1", "city"]
    permission_classes = [IsAuthenticated, IsSpecificUserType, IsAdminOrOwnData]
    pagination_class = DefaultPagination
    serializer_class = LeaseSerializer

    def get_base_queryset(self):
        """
        One query for the page, one for its documents, and the document count
        computed by the database.

        ``num_of_docs`` is a model method, so serializing a page of ten leases
        used to issue ten COUNT queries on top of ten more for the nested
        documents. The annotation shadows the method.
        """
        return (
            Lease.objects.all()
            .annotate(num_of_docs=Count("documents", distinct=True))
            .prefetch_related(Prefetch("documents", queryset=Document.objects.order_by("-version")))
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.action == "upload_lease":
            return LeaseUploadSerializer
        return LeaseSerializer

    @action(detail=False, methods=["post"], url_path="upload")
    def upload_lease(self, request):
        serializer = LeaseUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        address = dict(serializer.validated_data)
        files = address.pop("documents", [])
        try:
            lease = services.create_lease(user=request.user, address=address, files=files)
        except services.LeaseServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        lease = self.get_queryset().get(pk=lease.pk)
        return Response(LeaseSerializer(lease).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["put"], url_path="update")
    def update_lease(self, request, pk=None):
        lease = self.get_object()
        serializer = LeaseSerializer(lease, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["delete"], url_path="delete")
    def delete_lease(self, request, pk=None):
        self.get_object().delete()
        return Response({"status": "Lease deleted successfully"}, status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="revised")
    def revised_lease(self, request, pk=None):
        lease = self.get_object()
        serializer = RevisedLeaseUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            document_ids = services.attach_documents(
                lease=lease, files=serializer.validated_data.get("documents", [])
            )
        except services.LeaseServiceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(
            {"status": "revised documents uploaded", "document_ids": document_ids},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        # get_queryset() and not Lease.objects.all(): the latter bypassed
        # scoping and let any authenticated user page through every lease.
        leases = self.get_queryset()

        address = request.query_params.get("address")
        if address:
            leases = leases.filter(Q(address1__icontains=address) | Q(address2__icontains=address))

        start_date = request.query_params.get("start_date")
        if start_date:
            parsed = _parse_date(start_date)
            if parsed is None:
                return Response(
                    {"detail": "Invalid start date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            leases = leases.filter(created_at__gte=parsed)

        end_date = request.query_params.get("end_date")
        if end_date:
            parsed = _parse_date(end_date)
            if parsed is None:
                return Response(
                    {"detail": "Invalid end date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            leases = leases.filter(created_at__lte=parsed + timedelta(days=1))

        status_value = request.query_params.get("status")
        if status_value:
            leases = leases.filter(status=status_value)

        page = self.paginate_queryset(leases)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)

        serializer = self.get_serializer(leases, many=True)
        return Response(
            serializer.data if leases.exists() else {"detail": "No leases found."},
            status=status.HTTP_200_OK,
        )


class DocumentViewSet(OwnerScopedQuerysetMixin, viewsets.ModelViewSet):
    """
    Documents are reached through their lease, and a lease belongs to a user.

    ``owner_field`` spells that out so every action - including ones added
    later - is scoped. Without it any authenticated user could read, edit and
    delete any document in the system by guessing an id.
    """

    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = DefaultPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lease", "version"]
    search_fields = ["name"]
    owner_field = "lease__user"

    def get_base_queryset(self):
        return Document.objects.select_related("lease").order_by("-uploaded_at")

    @action(detail=False, methods=["get"], url_path=r"preview/(?P<document_id>\d+)")
    def preview_document(self, request, document_id=None):
        document = get_object_or_404(self.get_queryset(), pk=document_id)
        if not document.file_url:
            return Response(
                {"detail": "Document file not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response({"file_url": document.file_url}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path=r"lease/(?P<lease_id>\d+)/documents")
    def list_document_names(self, request, lease_id=None):
        documents = self.get_queryset().filter(lease_id=lease_id).order_by("version")
        if not documents.exists():
            return Response(
                {"detail": "No documents found for this lease."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            [
                {
                    "id": document.id,
                    "lease_id": document.lease_id,
                    "name": f"{document.name.split('/')[-1].rsplit('.', 1)[0]}_v{document.version}",
                    "uploaded_at": document.uploaded_at,
                    "status": document.status,
                    "file_url": document.file_url,
                }
                for document in documents
            ],
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="review")
    def review_documents(self, request):
        document_ids = request.data.get("document_ids", [])
        if not document_ids:
            return Response(
                {"detail": "No document IDs provided."}, status=status.HTTP_400_BAD_REQUEST
            )

        # Scoped lookup: this used to accept any document id in the system.
        documents = [
            get_object_or_404(self.get_queryset(), pk=document_id) for document_id in document_ids
        ]
        return Response(services.queue_review(documents), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="chat")
    def chat_with_gpt(self, request, pk=None):
        serializer = GPTChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        document_pk = resolve_detail_pk(
            pk, serializer.validated_data.get("document_id"), "document_id"
        )
        document = get_object_or_404(self.get_queryset(), pk=document_pk)
        try:
            payload = services.chat_about_document(
                document=document, message=serializer.validated_data["message"]
            )
        except services.LeaseServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(payload, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="get-chat-history")
    def get_chat_history(self, request, pk=None):
        document = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(services.chat_history_payload(document), status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="analysis")
    def analysis(self, request, pk=None):
        """The structured review: verdict, confidence, money, clause findings."""
        document = get_object_or_404(self.get_queryset(), pk=pk)
        payload = services.analysis_payload(document)
        if payload is None:
            return Response(
                {
                    "detail": "This document has not been reviewed yet.",
                    "status": document.status,
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(payload, status=status.HTTP_200_OK)


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
