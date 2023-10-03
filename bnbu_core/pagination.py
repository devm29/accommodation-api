"""Pagination used across the API."""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """
    Page size the client may choose, within a ceiling.

    Every list route in this project is paginated. An endpoint that can return
    "all rows" is a load test waiting for its first busy account.
    """

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_page_size(self, request):
        raw = request.query_params.get(self.page_size_query_param)
        if raw is None:
            return self.page_size
        try:
            page_size = int(raw)
        except (TypeError, ValueError):
            return self.page_size
        if page_size < 1:
            return self.page_size
        return min(page_size, self.max_page_size)
