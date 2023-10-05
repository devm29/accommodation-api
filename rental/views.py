"""
HTTP surface for the rental app.

Filter building, spreadsheet validation and CSV rendering live in
:mod:`rental.services`; these actions route and render.
"""

from __future__ import annotations

import logging

from celery.result import AsyncResult
from django.http import StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bnbu_core.mixins import OwnerScopedQuerysetMixin
from bnbu_core.pagination import DefaultPagination
from bnbu_core.permissions import IsOwnerByIdOrAdmin, IsSpecificUserType

from . import services
from .models import RentalProperty
from .serializers import RentalPropertySerializer

logger = logging.getLogger(__name__)


class RentalPropertyViewSet(OwnerScopedQuerysetMixin, viewsets.ModelViewSet):
    """
    ``RentalProperty`` stores its owner as a plain integer column, not a
    relation, so the owner field is named explicitly here and the object
    permission compares ids.
    """

    queryset = RentalProperty.objects.all()
    permission_classes = [IsAuthenticated, IsSpecificUserType, IsOwnerByIdOrAdmin]
    serializer_class = RentalPropertySerializer
    pagination_class = DefaultPagination
    owner_field = "user_id"

    def get_base_queryset(self):
        return RentalProperty.objects.all().order_by("-created_at")

    @action(detail=False, methods=["post"], url_path="upload-properties")
    def upload_rental_properties(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response(
                {"success": False, "message": "File not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            batch_id, task_id = services.start_ingest(user=request.user, uploaded_file=uploaded)
        except services.RentalValidationError as exc:
            return Response(
                {"success": False, "message": exc.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "success": True,
                "message": f"Batch {batch_id} processing started",
                "task_id": task_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path="task-result")
    def get_task_result(self, request):
        result = self._task(request)
        if result is None:
            return _missing_task_id()

        if result.state == "PENDING":
            return Response(
                {"success": False, "message": "Task is still pending"},
                status=status.HTTP_202_ACCEPTED,
            )
        if result.state == "FAILURE":
            return Response(
                {"success": False, "message": "Task failed", "error": str(result.result)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if result.state == "SUCCESS":
            return Response(
                {"success": True, "message": "Task completed", "result": result.result},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"success": True, "message": f"Task is {result.state}", "result": result.result},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="task-progress")
    def task_progress(self, request):
        result = self._task(request)
        if result is None:
            return _missing_task_id()

        if result.state == "PENDING":
            return Response(
                {"success": False, "state": "PENDING", "progress": 0},
                status=status.HTTP_202_ACCEPTED,
            )
        if result.state == "PROGRESS":
            info = result.info or {}
            return Response(
                {
                    "success": True,
                    "state": "PROGRESS",
                    "progress": info.get("progress", 0),
                    "message": info.get("message", "Processing..."),
                },
                status=status.HTTP_200_OK,
            )
        if result.state == "SUCCESS":
            return Response(
                {"success": True, "state": "SUCCESS", "progress": 100, "result": result.info},
                status=status.HTTP_200_OK,
            )
        if result.state == "FAILURE":
            return Response(
                {"success": False, "state": "FAILURE", "error": str(result.result)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {"success": False, "state": result.state},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @action(detail=False, methods=["get"], url_path="all-properties")
    def all_properties(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    @action(detail=False, methods=["post"], url_path="filtered-list")
    def filtered_list(self, request):
        try:
            query = services.build_filter(request.data)
        except services.RentalValidationError as exc:
            return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)

        properties = self.get_queryset().filter(query)
        page = self.paginate_queryset(properties)
        return self.get_paginated_response(
            {
                "all_batch_ids": services.batch_ids(properties),
                "properties": self.get_serializer(page, many=True).data,
            }
        )

    @action(detail=False, methods=["get"], url_path="download-csv")
    def download_csv(self, request):
        try:
            query = services.build_filter(request.query_params)
        except services.RentalValidationError as exc:
            return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)

        properties = self.get_queryset().filter(query)
        response = StreamingHttpResponse(
            services.stream_csv_rows(properties), content_type="text/csv"
        )
        response["Content-Disposition"] = 'attachment; filename="rental_properties.csv"'
        return response

    def _task(self, request):
        task_id = request.query_params.get("task_id")
        return AsyncResult(task_id) if task_id else None


def _missing_task_id():
    return Response(
        {"success": False, "message": "Task ID is required"},
        status=status.HTTP_400_BAD_REQUEST,
    )
