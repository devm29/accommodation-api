"""Account, authentication and user-administration routes."""

from django.urls import path

from .views import (
    ClientDetailView,
    ClientListCreateView,
    CustomerCreateView,
    CustomerDetailView,
    CustomerListView,
    DashboardView,
    FirstTimePasswordUpdateView,
    LoginView,
    LogoutView,
    UserDetailView,
    UserListCreateView,
    password_change,
    password_reset,
    user_profile,
)

urlpatterns = [
    path("users/", UserListCreateView.as_view(), name="user-list-create"),
    path("users/<int:pk>/", UserDetailView.as_view(), name="user-detail"),
    path(
        "users/update-password/<int:pk>/",
        FirstTimePasswordUpdateView.as_view(),
        name="update-password",
    ),
    path("clients/", ClientListCreateView.as_view(), name="client-list-create"),
    path("clients/<int:pk>/", ClientDetailView.as_view(), name="client-detail"),
    path("clients/<int:client_id>/customers/", CustomerListView.as_view(), name="customer-list"),
    path("customers/", CustomerListView.as_view(), name="all-customer-list"),
    path(
        "clients/<int:client_id>/customers/create/",
        CustomerCreateView.as_view(),
        name="customer-create",
    ),
    path(
        "clients/<int:client_id>/customers/<int:pk>/",
        CustomerDetailView.as_view(),
        name="customer-detail",
    ),
    path("password/change/", password_change, name="password-change"),
    path("password/reset/", password_reset, name="password-reset"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    # This was django.contrib.auth.views.LoginView, a template-rendering view.
    # The project ships no templates and no `registration/login.html`, so GET
    # /account/login/ raised TemplateDoesNotExist and POST redirected instead
    # of returning JSON. The API LoginView below was already imported and its
    # LogoutView counterpart already routed.
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("profile/", user_profile, name="user-profile"),
]
