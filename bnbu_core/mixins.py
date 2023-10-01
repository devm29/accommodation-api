"""Viewset mixins shared by the four apps."""

from __future__ import annotations


class OwnerScopedQuerysetMixin:
    """
    Narrow every queryset to the caller unless they are staff.

    Object-level permissions do not run on list routes, so scoping has to
    happen in the queryset. Doing it here means a new action on one of these
    viewsets is scoped by default rather than by remembering.

    Set ``owner_field`` to ``"user_id"`` on models that store the owner as a
    plain integer column.
    """

    owner_field = "user"

    def get_base_queryset(self):
        return super().get_queryset()

    def get_queryset(self):
        queryset = self.get_base_queryset()
        user = getattr(self.request, "user", None)
        # drf-yasg introspects viewsets with an anonymous request to build the
        # schema. There is nothing to scope to, and nothing to show.
        if getattr(self, "swagger_fake_view", False) or not (user and user.is_authenticated):
            return queryset.none()
        if user.is_staff:
            return queryset
        if self.owner_field.endswith("_id"):
            return queryset.filter(**{self.owner_field: user.id})
        return queryset.filter(**{self.owner_field: user})


def resolve_detail_pk(pk, body_id, field_name: str) -> int:
    """
    Reconcile the id in a detail URL with a duplicate id in the request body.

    Several chat actions are routed ``detail=True`` but historically read the
    target id out of the body and ignored the URL, so
    ``POST /api/documents/5/chat/`` with ``{"document_id": 9}`` acted on 9.
    The URL wins now; a body that disagrees is a 400 rather than a surprise.

    Raises ``rest_framework.serializers.ValidationError`` on a mismatch.
    """
    from rest_framework import serializers

    try:
        url_pk = int(pk)
    except (TypeError, ValueError):
        raise serializers.ValidationError({field_name: "Invalid id in the URL."})
    if body_id is not None and int(body_id) != url_pk:
        raise serializers.ValidationError(
            {field_name: f"Does not match the id in the URL ({url_pk})."}
        )
    return url_pk
