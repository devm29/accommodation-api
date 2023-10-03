"""Shared DRF permissions.

These three classes were copied verbatim into ``lease``, ``regulations`` and
``rental``, which meant a fix in one place (``rental`` owns its user as a
plain integer column, not a relation) had to be remembered in three.
"""

from __future__ import annotations

from rest_framework import permissions

ALLOWED_USER_TYPES = ("client", "admin", "research", "coach", "customer")


class IsSpecificUserType(permissions.BasePermission):
    """Allow only the product's own user types through."""

    allowed_user_types = ALLOWED_USER_TYPES

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return getattr(user, "user_type", None) in self.allowed_user_types


class IsAdminOrOwnData(permissions.BasePermission):
    """
    Staff reach every row; everyone else reaches only their own.

    Object-level only, which is why every viewset that uses it also scopes its
    queryset - DRF never runs object permissions on a list route.
    """

    #: Attribute on the object that identifies the owner.
    owner_field = "user"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        owner_field = getattr(view, "owner_field", self.owner_field)
        if owner_field.endswith("_id"):
            return getattr(obj, owner_field, None) == request.user.id
        return getattr(obj, owner_field, None) == request.user


class IsOwnerByIdOrAdmin(IsAdminOrOwnData):
    """For models that store the owner as a bare integer column."""

    owner_field = "user_id"
