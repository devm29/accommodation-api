"""URL routing for the whole API."""

from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

schema_view = get_schema_view(
    openapi.Info(
        title="BnBu API",
        default_version="v1",
        description=(
            "Short-term-rental investment analysis: spreadsheet-driven property "
            "underwriting, GPT lease review, and short-term-let regulation lookups."
        ),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("account/", include("accounts.urls")),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", include("bnbu_core.urls")),
    path("api/", include("lease.urls")),
    path("api/", include("regulations.urls")),
    path("api/", include("rental.urls")),
    # drf-yasg was installed and its static files collected, but no schema URL
    # was ever routed, so the project shipped with no live API documentation.
    path("api/docs/", schema_view.with_ui("swagger", cache_timeout=0), name="swagger-ui"),
    path("api/redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="redoc"),
    path(
        "api/schema.json",
        schema_view.without_ui(cache_timeout=0),
        name="openapi-schema",
    ),
]
