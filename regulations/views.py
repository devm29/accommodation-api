"""
HTTP surface for the regulations app.

The prompt, the classification, the caching and the persistence used to live
in this file. They now live in :mod:`regulations.services` and
:mod:`bnbu_core.regulation_sources`; what is left is routing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bnbu_core.mixins import OwnerScopedQuerysetMixin, resolve_detail_pk
from bnbu_core.pagination import DefaultPagination
from bnbu_core.permissions import IsAdminOrOwnData, IsSpecificUserType

from . import services
from .models import Regulations
from .serializers import GPTChatSerializer, RegulationsSerializer

logger = logging.getLogger(__name__)


class RegulationsViewSet(OwnerScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Regulations.objects.all()
    filter_backends = [SearchFilter, OrderingFilter, DjangoFilterBackend]
    # SearchFilter owns ``?search=`` (free text over the saved query);
    # DjangoFilterBackend owns the exact-match filters. Listing "search" in
    # both declared the same query parameter twice.
    search_fields = ["search", "status"]
    filterset_fields = ["status", "analysis_state"]
    permission_classes = [IsAuthenticated, IsSpecificUserType, IsAdminOrOwnData]
    pagination_class = DefaultPagination
    serializer_class = RegulationsSerializer

    def get_base_queryset(self):
        return Regulations.objects.all().order_by("-created_at")

    def create(self, request, *args, **kwargs):
        """
        Save the search and answer it, or queue it.

        A cached or curated answer comes back attached to the row. Anything
        needing a model call comes back with ``analysis_state: "pending"`` and
        is filled in by a worker - the client polls the row it already has.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        regulation = services.create_regulation(
            user=request.user, search=serializer.validated_data["search"]
        )
        body = self.get_serializer(regulation).data
        code = (
            status.HTTP_201_CREATED
            if regulation.analysis_state == Regulations.STATE_COMPLETE
            else status.HTTP_202_ACCEPTED
        )
        return Response(body, status=code)

    @action(detail=True, methods=["post"], url_path="reanalyze")
    def reanalyze(self, request, pk=None):
        """Re-run the lookup for one saved search, ignoring the cache."""
        regulation = self.get_object()
        services.clear_cached_answer(regulation.search)
        services.run_analysis(regulation)
        return Response(self.get_serializer(regulation).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="chat")
    def chat_with_gpt(self, request, pk=None):
        serializer = GPTChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Scoped through get_queryset(): a bare lookup let any authenticated
        # user read and append to another user's analysis and chat history.
        regulation_pk = resolve_detail_pk(
            pk, serializer.validated_data.get("regulation_id"), "regulation_id"
        )
        regulation = get_object_or_404(self.get_queryset(), pk=regulation_pk)
        try:
            payload = services.chat(
                regulation=regulation, message=serializer.validated_data["message"]
            )
        except services.RegulationServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(payload, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="get-chat-history")
    def get_chat_history(self, request, pk=None):
        regulation = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(services.chat_history_payload(regulation), status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="search")
    def search(self, request):
        # get_queryset() and not Regulations.objects.all(): the latter skipped
        # scoping and returned every user's searches to anyone.
        regulations = self.get_queryset()

        query = request.query_params.get("query")
        if query:
            regulations = regulations.filter(search__icontains=query)

        start_date = request.query_params.get("start_date")
        if start_date:
            parsed = _parse_date(start_date)
            if parsed is None:
                return Response(
                    {"detail": "Invalid start date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            regulations = regulations.filter(created_at__gte=parsed)

        end_date = request.query_params.get("end_date")
        if end_date:
            parsed = _parse_date(end_date)
            if parsed is None:
                return Response(
                    {"detail": "Invalid end date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            regulations = regulations.filter(created_at__lte=parsed + timedelta(days=1))

        status_value = request.query_params.get("status")
        if status_value:
            regulations = regulations.filter(status=status_value)

        page = self.paginate_queryset(regulations)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)

        serializer = self.get_serializer(regulations, many=True)
        return Response(
            serializer.data if regulations.exists() else {"detail": "No regulations found."},
            status=status.HTTP_200_OK,
        )


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
