"""
HTTP surface for the accounts app.

Every list route here is paginated. An admin endpoint that serialises the
whole user table in one response is fine with fifty accounts and a timeout
with fifty thousand.
"""

from __future__ import annotations

from django.contrib.auth import login, logout
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveUpdateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from bnbu_core.pagination import DefaultPagination

from . import services
from .models import CustomUser
from .serializers import (
    CustomerSerializer,
    FirstTimePasswordUpdateSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    UserSerializer,
)


class PaginatedListMixin:
    """Render a queryset as one paginated page."""

    pagination_class = DefaultPagination

    def paginated(self, request, queryset, serializer_class):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(serializer_class(page, many=True).data)


class UserListCreateView(PaginatedListMixin, APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return self.paginated(request, CustomUser.objects.all().order_by("id"), UserSerializer)

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()
        # Newly created users should be active by default for the admin workflow.
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UserDetailView(RetrieveUpdateDestroyAPIView):
    queryset = CustomUser.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]

    def put(self, request, *args, **kwargs):
        super().put(request, *args, **kwargs)
        return Response({"message": "User updated successfully"}, status=status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):
        super().delete(request, *args, **kwargs)
        return Response({"message": "User deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


class ClientListCreateView(PaginatedListMixin, APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return self.paginated(
            request, CustomUser.objects.filter(user_type="client").order_by("id"), UserSerializer
        )

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ClientDetailView(RetrieveUpdateDestroyAPIView):
    queryset = CustomUser.objects.filter(user_type="client")
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser]

    def put(self, request, *args, **kwargs):
        super().put(request, *args, **kwargs)
        return Response({"message": "Client updated successfully"}, status=status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):
        client = self.get_object()
        # A client's customers exist only in relation to it.
        CustomUser.objects.filter(client=client).delete()
        self.perform_destroy(client)
        return Response(
            {"message": "Client and associated customers deleted successfully"},
            status=status.HTTP_204_NO_CONTENT,
        )


class CustomerListView(PaginatedListMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_id=None):
        if request.user.user_type == "admin" and client_id is not None:
            # 404 rather than an empty page when the client does not exist.
            get_object_or_404(CustomUser, id=client_id, user_type="client")
        customers = services.customers_for(request.user, client_id)
        return self.paginated(request, customers, CustomerSerializer)


class CustomerCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, client_id):
        # request.data is an immutable QueryDict for form-encoded posts.
        data = request.data.copy()
        # An admin names the client; anyone else always gets themselves.
        data["client"] = client_id if request.user.user_type == "admin" else request.user.id

        if not data.get("client"):
            return Response({"error": "Client ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = CustomerSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomerDetailView(RetrieveUpdateDestroyAPIView):
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]
    # get_object() does its own lookup, but DRF still requires a queryset for
    # schema generation: without it drf-yasg raised on every build and this
    # route was missing from /api/docs/.
    queryset = CustomUser.objects.filter(user_type="customer")

    def get_object(self):
        customer = get_object_or_404(CustomUser, pk=self.kwargs["pk"], user_type="customer")
        if self.request.user.user_type == "admin":
            return customer
        if customer.client != self.request.user:
            raise PermissionDenied("You do not have permission to access this customer.")
        return customer

    def put(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Customer updated successfully"}, status=status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(
            {"message": "Customer deleted successfully"}, status=status.HTTP_204_NO_CONTENT
        )


class FirstTimePasswordUpdateView(RetrieveUpdateAPIView):
    serializer_class = FirstTimePasswordUpdateSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "pk"
    queryset = CustomUser.objects.all()

    def post(self, request, *args, **kwargs):
        user = self.get_object()

        if request.user != user:
            return Response(
                {"error": "You are not allowed to update this user's password."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not user.is_first_login:
            return Response(
                {"error": "Password update not allowed. This is not your first login."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Password updated successfully"}, status=status.HTTP_200_OK)


class LoginView(APIView):
    def post(self, request):
        user = services.authenticate_by_email(
            email=request.data.get("email"), password=request.data.get("password")
        )
        if user is None:
            return Response(
                {"error": "Invalid email or password"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Two backends are configured and this user did not come from
        # authenticate(), so the backend has to be named explicitly.
        login(request, user, backend=services.AUTH_BACKEND)
        if services.needs_password_change(user):
            return Response(
                {"message": "First login, password update required"},
                status=status.HTTP_200_OK,
            )
        return Response({"message": "Logged in successfully"}, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({"message": "Logged out successfully"}, status=status.HTTP_200_OK)


class DashboardView(PaginatedListMixin, APIView):
    """
    The landing payload for whoever is asking.

    The admin branch used to serialise every user in the database in one
    unpaginated response.
    """

    permission_classes = [IsAuthenticated]

    GREETINGS = {
        "coach": "Welcome to coach dashboard",
        "research": "Welcome to research dashboard",
        "client": "Welcome to client dashboard",
    }

    def get(self, request):
        if request.user.user_type == "admin":
            return self.paginated(request, CustomUser.objects.all().order_by("id"), UserSerializer)
        message = self.GREETINGS.get(request.user.user_type, "Welcome to general dashboard")
        return Response({"message": message}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def password_change(request):
    serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
    if serializer.is_valid():
        serializer.save()
        return Response({"success": "Password changed successfully"}, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def password_reset(request):
    serializer = PasswordResetSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if not services.send_password_reset(serializer.validated_data["email"]):
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"message": "Password reset email sent"}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def user_profile(request):
    return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)
